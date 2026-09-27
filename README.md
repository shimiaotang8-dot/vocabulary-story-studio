# 词间 · Vocabulary Story Studio

把正在学习的英语词汇放进一篇连贯的小说故事里。支持中文叙事或英文叙事、自动/自定义背景、个人词库、CSV 导出和故事书架。

## 功能

- 逐个添加单词，或按行、逗号批量粘贴。
- 生成故事时要求目标词自然融入情节，并附简短中文释义。
- 未指定背景时，AI 根据词汇联想故事类型；也可以保存自定义背景。
- 词库按添加日期分组，可批量选择生成故事、导出 CSV、批量删除。
- 每次生成后由用户选择是否保存到书架。
- 通过 OpenAI-compatible Chat Completions API 生成故事、查询不认识词汇的中文释义；结果保存在本地缓存，减少重复调用。
- 本地用户名、词库、生成统计和书架保存在 SQLite 数据库中。

## 环境要求

- Python 3.10 或更高版本（服务端仅使用 Python 标准库，无需安装依赖）。
- 兼容 OpenAI Chat Completions 格式的模型 API 用于生成故事和查询未收录词汇。没有 API Key 时仍可打开工作台并使用离线故事模板。

## 配置模型 API

1. 将 `.env.example` 复制为 `.env`，文件放在 `server.py` 同一目录。
2. 在 `.env` 中填写你自己的 API Key：

   ```dotenv
   AI_PROVIDER=DeepSeek
   AI_API_KEY=你的API_Key
   AI_BASE_URL=https://api.deepseek.com
   AI_MODEL=deepseek-chat
   ```

3. 保存后启动服务。不要把 `.env`、API Key 或 `wordstory.sqlite3` 提交到 GitHub。

DeepSeek 是默认示例，不是必选服务。换用其他模型服务时，将 `AI_PROVIDER`、`AI_API_KEY`、`AI_BASE_URL` 和 `AI_MODEL` 替换为该服务商提供的名称、密钥、兼容接口根地址和模型 ID。服务商需要支持 OpenAI-compatible `/chat/completions` 接口及 JSON mode；接口根地址通常要包含服务商文档要求的版本路径（例如 `/v1`）。不同服务商的模型能力、JSON mode 支持及计费规则不同，请以其 API 文档为准。旧版 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 仍可作为后备配置。

## 启动

### Windows

双击 `启动故事工作台.bat`，浏览器会打开 `http://127.0.0.1:8765`。黑色命令窗口是本地服务进程，使用期间不要关闭。`.bat` 只是 Windows 便捷入口，不是运行项目的必需文件。

也可以在 PowerShell 中运行：

```powershell
python server.py
```

然后访问 <http://127.0.0.1:8765>。

### macOS / Linux

```bash
python3 server.py
```

然后访问 <http://127.0.0.1:8765>。

## 数据与部署说明

这是一个本机运行的个人工具。服务只绑定 `127.0.0.1`；账户、词库、统计、故事书架和释义缓存保存在项目目录的 `wordstory.sqlite3`。密码使用 scrypt 哈希，登录会话可选 7 天或 30 天。数据不会自动同步到其他设备。

直接双击 `index.html` 可以体验离线工作台，但浏览器文件模式不提供账户、个人词库、书架或模型 API。GitHub Pages 也只能托管静态文件，不能运行本项目所需的本地 Python 服务。不要为了在线部署而把 API Key 写入 `app.js` 或任何前端文件；如需公开在线服务，应另外部署带有安全密钥管理和用户隔离的后端。

## 提示词

完整故事提示词由 `server.py` 的 `make_prompt()` 动态构建，包含角色和创作目标、语言模式、背景设定、情节质量标准、目标词清单、JSON 输出格式与生成前自检。目标词必须以原拼写出现在正文中并自然推动情节；中文模式用中文叙事，英文模式用英文叙事，目标词首次出现时附中文释义。
