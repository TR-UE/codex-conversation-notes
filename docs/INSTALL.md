# 安装与排错

## 安装包

解压 macOS 安装包，双击 `安装.command`。安装器复制插件到 `~/.local/share/codex-conversation-notes/plugin/conversation-notes/`，仅更新个人目录中的 `conversation-notes` 条目，然后调用本机 Codex CLI 安装。已有个人目录的名称和其他插件条目保留；现有笔记不在安装目录中，不会被覆盖。

重启 Codex / ChatGPT 桌面应用，在新聊天中选择墨记。已有聊天可能仍使用旧工具清单，此时可以双击 `打开墨记.command` 使用相同的本机数据。

## 文档导出环境

安装器优先寻找桌面应用的 `~/.cache/codex-runtimes/codex-primary-runtime/dependencies/`。该目录中的 Python 应提供 python-docx、Pillow 和 pypdf；Node.js 22+ 应提供 Sharp；PDF 还需要 LibreOffice。自动检测结果保存在插件本机的 `export-runtime.json` 中，配置不进入 Git 或发布包。

如果运行环境在其他位置，可以运行：

```sh
python3 scripts/configure_runtime.py --runtime-root /path/to/dependencies
```

也可分别指定：

```sh
python3 scripts/configure_runtime.py \
  --python /path/to/python3 \
  --node /path/to/node \
  --node-modules /path/to/node_modules \
  --soffice /path/to/soffice
```

上述命令在解压后的插件目录运行；源码仓库对应目录为 `plugin/conversation-notes/`。基础阅读、编辑、高亮和批注仅需 Python 标准库；未配置文档环境时，可以先使用这些功能和 JSON / Markdown 备份。

## 源码运行与测试

```sh
python3 plugin/conversation-notes/scripts/open_local.py
```

文档测试请使用检测出的 Python，例如：

```sh
PYTHONPATH=plugin/conversation-notes \
  ~/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m unittest discover -s plugin/conversation-notes/tests -v
```

构建浏览器和公式依赖时，把 `scripts/rich-package.json` 与锁文件放入独立 npm 项目，安装依赖后执行 `node scripts/build-rich.mjs /path/to/build-project`。插件运行使用已打包的产物。

## 常见问题

- 插件未出现在当前聊天：重启桌面应用，并在新聊天中选择墨记。
- 找不到 Codex CLI：安装支持本地插件的 Codex CLI，或设置 `CODEX_BINARY` 指向可执行文件后重试。
- PDF / Word 运行环境未齐备：运行上面的配置命令；安装器不会自动下载额外文档依赖。
- 远程图片未嵌入：网络或登录限制可能阻止下载；先保存到本机，再通过编辑区插入图片。
- 数据位置：`~/.codex/conversation-notes/`；备份可从面板“导出 → JSON”生成。卸载插件前可主动备份。

当前 macOS 中文 PDF 验证使用捆绑字体配置。其他系统、缺少中文字体的自定义运行环境尚未验证。
