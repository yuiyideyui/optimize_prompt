import { app } from "../../../scripts/app.js";

app.registerExtension({
    name: "comfy.DirectorTable",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "DirectorTableNode") {
            const origNodeCreated = nodeType.prototype.onNodeCreated;

            nodeType.prototype.onNodeCreated = function() {
                if (origNodeCreated) {
                    origNodeCreated.apply(this, arguments);
                }

                const node = this;
                
                // 1. 找到原生的 director_data 文本 Widget（不隐藏它，或者让它正常参与 ComfyUI 序列化）
                const dataWidget = node.widgets.find(w => w.name === "director_data");
                
                // 如果你想让界面更干净，可以把它的高度压扁或者让它正常显示
                if (dataWidget) {
                    dataWidget.inputEl.style.fontSize = "10px";
                    dataWidget.inputEl.style.height = "40px";
                    // 监听底层文本框变化（防止用户直接改文本）
                    const origOnChange = dataWidget.callback;
                    dataWidget.callback = (val) => {
                        if (origOnChange) origOnChange(val);
                        syncUIFromText();
                    };
                }

                // 2. 创建导演台卡片容器 DOM
                const container = document.createElement("div");
                container.className = "director-table-container";
                container.style.cssText = `
                    display: flex; flex-direction: column; gap: 8px; padding: 6px;
                    background: #1e1e1e; border-radius: 8px; color: #fff;
                    font-family: sans-serif; max-height: 360px; overflow-y: auto;
                    margin-bottom: 5px;
                `;

                // 顶部操作栏
                const header = document.createElement("div");
                header.style.cssText = "display: flex; justify-content: space-between; align-items: center;";
                header.innerHTML = `<span style="font-weight: bold; font-size: 12px;">🎬 导演台卡片面板</span>`;
                
                const addBtn = document.createElement("button");
                addBtn.innerText = "+ 添加卡片";
                addBtn.style.cssText = `
                    background: #4a4a4a; color: white; border: none; 
                    padding: 3px 6px; border-radius: 4px; cursor: pointer; font-size: 10px;
                `;
                header.appendChild(addBtn);
                container.appendChild(header);

                // 卡片网格展示区
                const gridArea = document.createElement("div");
                gridArea.style.cssText = "display: flex; flex-wrap: wrap; gap: 6px;";
                container.appendChild(gridArea);

                let cardsData = [];

                // 从文本框加载数据到卡片界面
                function syncUIFromText() {
                    try {
                        if (dataWidget && dataWidget.value) {
                            cardsData = JSON.parse(dataWidget.value);
                        } else {
                            cardsData = [];
                        }
                    } catch(e) {
                        cardsData = [];
                    }
                    renderGrid();
                }

                // 将卡片数据同步保存回标准文本框（这样 ComfyUI 的 Save 才能保存住！）
                function syncTextFromUI() {
                    if (dataWidget) {
                        dataWidget.value = JSON.stringify(cardsData);
                        // 触发 ComfyUI 的改变事件，标记工作流已被修改需要保存
                        if (dataWidget.callback) {
                            dataWidget.callback(dataWidget.value);
                        }
                    }
                    node.setDirtyCanvas(true, true);
                }

                // 渲染网格
                function renderGrid() {
                    gridArea.innerHTML = "";
                    cardsData.forEach((card, index) => {
                        const cardEl = document.createElement("div");
                        cardEl.style.cssText = `
                            position: relative; width: 85px; background: #2a2a2a;
                            border: 1px solid #444; border-radius: 6px; padding: 4px;
                            display: flex; flex-direction: column; align-items: center; gap: 3px;
                        `;

                        // 删除按钮
                        const closeBtn = document.createElement("div");
                        closeBtn.innerText = "×";
                        closeBtn.style.cssText = `
                            position: absolute; top: 1px; right: 3px;
                            cursor: pointer; font-weight: bold; color: #ff5555; font-size: 13px;
                            z-index: 5;
                        `;
                        closeBtn.onclick = () => {
                            cardsData.splice(index, 1);
                            syncTextFromUI();
                            renderGrid();
                        };
                        cardEl.appendChild(closeBtn);

                        // 缩略图区域
                        const thumb = document.createElement("div");
                        thumb.style.cssText = `
                            width: 75px; height: 75px; background: #333; 
                            border-radius: 4px; display: flex; align-items: center; justify-content: center;
                            font-size: 8px; text-align: center; color: #aaa; overflow: hidden;
                            border: 1px dashed #555; cursor: pointer; position: relative;
                        `;
                        
                        if (card.filename) {
                            const imgPreview = document.createElement("img");
                            const sub = card.subfolder || "";
                            imgPreview.src = `/view?filename=${encodeURIComponent(card.filename)}&type=input&subfolder=${encodeURIComponent(sub)}`;
                            imgPreview.style.cssText = "width: 100%; height: 100%; object-fit: cover;";
                            thumb.innerHTML = "";
                            thumb.appendChild(imgPreview);
                        } else {
                            thumb.innerText = "拖拽图片至此\n或点击";
                        }

                        // 拖拽上传事件
                        thumb.addEventListener("dragover", (e) => { e.preventDefault(); e.stopPropagation(); });
                        thumb.addEventListener("drop", (e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            if (e.dataTransfer.files.length > 0 && e.dataTransfer.files[0].type.startsWith("image/")) {
                                handleCardFileUpload(e.dataTransfer.files[0], index);
                            }
                        });

                        // 点击选择图片
                        thumb.addEventListener("click", () => {
                            const fileInput = document.createElement('input');
                            fileInput.type = 'file';
                            fileInput.accept = 'image/*';
                            fileInput.onchange = (e) => {
                                if (e.target.files.length > 0) {
                                    handleCardFileUpload(e.target.files[0], index);
                                }
                            };
                            fileInput.click();
                        });

                        cardEl.appendChild(thumb);

                        // 角色标签输入框
                        const roleInput = document.createElement("input");
                        roleInput.type = "text";
                        roleInput.value = card.role || "";
                        roleInput.placeholder = "标签";
                        roleInput.style.cssText = `
                            width: 72px; background: #111; color: #fff; 
                            border: 1px solid #555; border-radius: 3px; font-size: 9px; text-align: center;
                        `;
                        roleInput.oninput = (e) => {
                            cardsData[index].role = e.target.value;
                            syncTextFromUI();
                        };
                        cardEl.appendChild(roleInput);

                        gridArea.appendChild(cardEl);
                    });
                }

                // 异步上传图片
                async function handleCardFileUpload(file, index) {
                    try {
                        const formData = new FormData();
                        formData.append("image", file);
                        formData.append("overwrite", "true");

                        const response = await fetch("/upload/image", { method: "POST", body: formData });
                        if (!response.ok) throw new Error("上传失败");

                        const result = await response.json();
                        cardsData[index].filename = result.name;
                        cardsData[index].subfolder = result.subfolder || "";
                        
                        syncTextFromUI();
                        renderGrid();
                    } catch (err) {
                        console.error(err);
                        alert("图片上传失败");
                    }
                }

                addBtn.onclick = () => {
                    cardsData.push({ filename: "", role: `角色 ${cardsData.length + 1}`, subfolder: "" });
                    syncTextFromUI();
                    renderGrid();
                };

                // 初始化时同步一次
                syncUIFromText();

                // 将导演台 DOM 挂载到节点上
                node.addDOMWidget("director_table_ui", "director_ui", container);
                node.size = [360, 320];
            };
        }
    }
});