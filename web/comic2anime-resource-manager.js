import { app } from "/scripts/app.js";

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[char]));

async function openManager() {
  const root = document.createElement("div");
  root.style.cssText = "position:fixed;z-index:100000;inset:4%;background:#17191b;color:#e8e8e8;padding:18px;overflow:auto;border:1px solid #555;font:13px sans-serif";
  root.innerHTML = `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><strong style="font-size:18px">Comic2Anime 资源</strong>
    <input id="project" placeholder="project_id" style="width:220px"><button id="load">加载</button>
    <select id="chapter"><option value="">全部章节</option></select><select id="page"><option value="">全部页面</option></select>
    <span id="summary" style="margin-left:auto"></span><button id="close">关闭</button></div>
    <div id="error" style="color:#ff8888;margin:10px 0"></div>
    <div style="overflow:auto"><table style="width:100%;border-collapse:collapse;text-align:left"><thead><tr>
    <th>预览</th><th>章节 / 页面</th><th>名称</th><th>资产</th><th>类型</th><th>状态</th><th>实体</th><th>创建时间</th><th></th>
    </tr></thead><tbody id="assets"></tbody></table></div>`;
  document.body.appendChild(root);
  root.querySelector("#close").onclick = () => root.remove();
  let data = null;
  const chapterSelect = root.querySelector("#chapter");
  const pageSelect = root.querySelector("#page");
  const renderPages = () => {
    const chapterId = chapterSelect.value;
    const pages = (data?.pages || []).filter((page) => !chapterId || page.chapter_id === chapterId);
    pageSelect.innerHTML = '<option value="">全部页面</option>' + pages.map((page) =>
      `<option value="${esc(page.page_id)}">第 ${esc(page.page_index)} 页</option>`).join("");
    renderAssets();
  };
  const renderAssets = () => {
    if (!data) return;
    const chapterId = chapterSelect.value;
    const pageId = pageSelect.value;
    const pages = data.pages.filter((page) => (!chapterId || page.chapter_id === chapterId) && (!pageId || page.page_id === pageId));
    const pageLabels = new Map(pages.map((page) => [page.page_id, `${page.chapter_id} / 第 ${page.page_index} 页`]));
    const assets = data.assets.filter((asset) => {
      const sourcePage = asset.metadata?.source_page_id || asset.metadata?.page_id;
      if (pageId) return sourcePage === pageId;
      if (chapterId) return !sourcePage || pageLabels.has(sourcePage);
      return true;
    });
    root.querySelector("#summary").textContent = `章节 ${data.chapters.length} · 页面 ${data.pages.length} · 当前资产 ${assets.length}`;
    root.querySelector("#assets").innerHTML = assets.map((asset) => {
      const sourcePage = asset.metadata?.source_page_id || asset.metadata?.page_id;
      const pageLabel = pageLabels.get(sourcePage) || (sourcePage ? "其他页面" : "未关联页面");
      const name = asset.entity_name || asset.display_name || asset.asset_id;
      return `<tr style="border-top:1px solid #393c3f"><td><img loading="lazy" src="/comic2anime/resources/${encodeURIComponent(data.project_id)}/${encodeURIComponent(asset.asset_id)}/image" style="width:112px;height:80px;object-fit:contain;background:#292b2d"></td>
      <td>${esc(pageLabel)}</td><td>${esc(name)}</td><td>${esc(asset.display_name || asset.asset_id)}<br><small>${esc(asset.asset_id)}</small></td><td>${esc(asset.asset_type)}</td><td>${esc(asset.status)}</td>
      <td>${esc(asset.entity_type)}:${esc(asset.entity_id)}</td><td>${esc(asset.created_at)}</td><td><button data-delete="${esc(asset.asset_id)}">删除</button></td></tr>`;
    }).join("") || '<tr><td colspan="9" style="padding:20px">没有匹配的资产</td></tr>';
    root.querySelectorAll("[data-delete]").forEach((button) => button.addEventListener("click", async () => {
      if (!window.confirm("删除此生成资产及对应文件？漫画原页和分析数据会保留。")) return;
      const response = await fetch(`/comic2anime/resources/${encodeURIComponent(data.project_id)}/${encodeURIComponent(button.dataset.delete)}`, { method: "DELETE" });
      if (!response.ok) { root.querySelector("#error").textContent = await response.text(); return; }
      data.assets = data.assets.filter((asset) => asset.asset_id !== button.dataset.delete);
      renderAssets();
    }));
  };
  chapterSelect.onchange = renderPages;
  pageSelect.onchange = renderAssets;
  root.querySelector("#load").onclick = async () => {
    const project = root.querySelector("#project").value.trim();
    if (!project) return;
    const response = await fetch(`/comic2anime/resources?project_id=${encodeURIComponent(project)}`);
    if (!response.ok) { root.querySelector("#error").textContent = await response.text(); return; }
    data = await response.json();
    chapterSelect.innerHTML = '<option value="">全部章节</option>' + data.chapters.map((chapter) =>
      `<option value="${esc(chapter.chapter_id)}">第 ${esc(chapter.chapter_index)} 话</option>`).join("");
    renderPages();
  };
}

app.registerExtension({
  name: "Comic2Anime.ResourceManager",
  setup() {
    const addButton = () => {
      if (document.getElementById("comic2anime-resource-manager-button")) return;
      const button = document.createElement("button");
      button.id = "comic2anime-resource-manager-button";
      button.textContent = "Comic2Anime 资源";
      button.title = "管理各话漫画页生成的资源";
      button.style.cssText = "position:fixed;right:16px;top:12px;z-index:99999";
      button.onclick = openManager;
      document.body.appendChild(button);
    };
    if (document.body) addButton();
    else window.addEventListener("DOMContentLoaded", addButton, { once: true });
  },
});
