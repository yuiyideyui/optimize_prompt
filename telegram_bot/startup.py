import sys
import argparse
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram_bot.config import TelegramConfig
from telegram_bot.bot_handler import create_telegram_webhook_handler, TelegramBotHandler


def start_polling(bot_token: str, poll_interval: int = 1):
    """启动 Polling 模式获取消息"""
    import json
    import urllib.request
    import urllib.error
    import time

    api_base = "https://api.telegram.org/bot"
    update_offset = 0
    bot_handler = TelegramBotHandler()

    print("✅ Polling 已启动")
    print("   正在监听消息...\n")

    try:
        while True:
            try:
                # 获取新消息
                url = f"{api_base}{bot_token}/getUpdates?offset={update_offset}&timeout=30"
                with urllib.request.urlopen(url, timeout=40) as response:
                    result = json.loads(response.read().decode("utf-8"))

                    if result.get("ok"):
                        updates = result.get("result", [])
                        for update in updates:
                            # 处理 update
                            bot_handler.handle_update(update)
                            # 更新 offset
                            update_offset = update.get("update_id", 0) + 1
                    else:
                        print(f"❌ API 错误: {result.get('description')}")

                time.sleep(poll_interval)

            except urllib.error.URLError as e:
                print(f"⚠️  网络错误: {e}，5 秒后重试...")
                time.sleep(5)
            except Exception as e:
                print(f"❌ 错误: {e}，5 秒后重试...")
                time.sleep(5)

    except KeyboardInterrupt:
        print("\n👋 Polling 已停止")


def main():
    parser = argparse.ArgumentParser(description="启动 Telegram 人工审核 Bot")
    parser.add_argument(
        "--token",
        type=str,
        help="Telegram Bot Token (或设置环境变量 TELEGRAM_BOT_TOKEN)",
    )
    parser.add_argument(
        "--user_id",
        type=str,
        help="你的 Telegram 用户 ID (或设置环境变量 TELEGRAM_USER_ID)",
    )
    parser.add_argument(
        "--webhook_url",
        type=str,
        default="",
        help="ComfyUI webhook URL (可选，用于将审核结果回传给 ComfyUI)",
    )
    parser.add_argument(
        "--webhook_port",
        type=int,
        default=8765,
        help="HTTP webhook 服务器端口 (默认: 8765)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["webhook", "polling"],
        default="webhook",
        help="运行模式: webhook (使用 HTTP 服务器) 或 polling (轮询)",
    )

    args = parser.parse_args()

    # 保存配置
    config = TelegramConfig()
    if args.token:
        config.set("bot_token", args.token)
    if args.user_id:
        config.set("user_id", args.user_id)
    if args.webhook_url:
        config.set("webhook_url", args.webhook_url)

    # 验证配置
    if not config.is_configured():
        print("❌ 错误: 缺少 Telegram Bot Token 或 User ID")
        print("\n配置方式:")
        print("  1. 命令行: python -m telegram_bot.startup --token XXX --user_id YYY")
        print("  2. 环境变量:")
        print("     export TELEGRAM_BOT_TOKEN=your_bot_token")
        print("     export TELEGRAM_USER_ID=your_user_id")
        print("\n获取 Bot Token:")
        print("  1. 在 Telegram 中搜索 @BotFather")
        print("  2. 发送 /newbot 创建新 bot")
        print("  3. 复制 Token")
        print("\n获取 User ID:")
        print("  1. 在 Telegram 中搜索 @userinfobot")
        print("  2. 发送任意消息，bot 会返回你的 ID")
        sys.exit(1)

    print("✅ Telegram Bot 已配置")
    print(f"   Bot Token: {config.get_bot_token()[:20]}...")
    print(f"   User ID: {config.get_user_id()}")

    if args.mode == "webhook":
        print(f"\n🚀 启动 Webhook 模式，端口 {args.webhook_port}")
        print(f"   本地地址: http://localhost:{args.webhook_port}/telegram")
        print("\n   如果需要外网访问，请使用 ngrok 或其他反向代理:")
        print(f"   ngrok http {args.webhook_port}")
        print("\n   然后在 Telegram 中设置 webhook:")
        print('   curl -X POST https://api.telegram.org/bot<TOKEN>/setWebhook \\')
        print('     -H "Content-Type: application/json" \\')
        print(
            '     -d \'{"url":"http://YOUR_PUBLIC_IP:' + str(args.webhook_port) + '/telegram"}\''
        )

        server = create_telegram_webhook_handler(webhook_port=args.webhook_port)
        if server:
            print("\n✅ Webhook 服务器已启动")
            print("   等待 Telegram 回调...")
            try:
                import time

                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n\n👋 已停止")
        else:
            print("❌ Webhook 服务器启动失败")
            sys.exit(1)
    else:
        print("\n📱 启动 Polling 模式")
        print("   Bot 将主动从 Telegram 获取消息\n")

        # 导入并启动 polling
        try:
            start_polling(config.get_bot_token())
        except KeyboardInterrupt:
            print("\n\n👋 已停止")
        except Exception as e:
            print(f"❌ Polling 启动失败: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
