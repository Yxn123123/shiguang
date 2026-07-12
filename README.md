# 拾光

一个给个人使用的手机优先 PWA：每天自动从公开资料中寻找候选内容，经 AI 提炼和二次质量审核后，生成短知识卡并发布。

## 正式版结构

- `site/`：手机网页与 PWA
- `site/data/cards.json`：已经审核通过的知识卡
- `scripts/generate_cards.py`：自动获取、AI提炼、二次审核、去重
- `.github/workflows/publish.yml`：每天定时更新并发布到 GitHub Pages

## 内容流程

```text
公开来源
→ 获取候选材料
→ AI判断有没有真正有趣的具体事实
→ AI二次质量审核
→ 本地规则再次检查
→ 去重
→ 最多新增8条
→ 更新 cards.json
→ 自动发布
```

程序会主动拒绝：

- “X是什么”
- “X有什么值得注意的地方”
- 普通人物简介
- 机构、职位和国家概况
- 军舰或设备参数罗列
- 只有日期、没有意外点的内容
- 无法被来源原文支持的事实

## 当前来源

- 中文维基百科“你知道吗？”
- Wikimedia On This Day
- NASA Astronomy Picture of the Day
- The Metropolitan Museum of Art Collection API

来源只是候选素材。没有足够好的知识点时，AI应当拒绝生成。

## 本地预览

在项目根目录运行：

```bash
python -m http.server 8000 --directory site
```

然后打开：

```text
http://localhost:8000
```

## 自动更新需要的 Secret

必须：

- `OPENAI_API_KEY`

可选：

- `NASA_API_KEY`

没有 `NASA_API_KEY` 时会使用 NASA 的 `DEMO_KEY`。

## 手机记录

收藏、历史、不感兴趣记录保存在浏览器本地，并支持导出和导入 JSON 备份。
