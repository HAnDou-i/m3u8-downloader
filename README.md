# M3U8 Downloader — NAS Web Edition

Docker 化的 M3U8 视频下载工具，适用于飞牛 NAS、群晖、Unraid 及任何支持 Docker 的 Linux 服务器。

## 功能

- 输入 M3U8 链接即可下载
- 自动探测多画质源，支持画质选择
- 支持自定义 Referer 和 Cookie（应对防盗链和登录限制）
- 实时下载进度、速度、剩余时间
- 任务队列管理：取消、删除、批量清除
- 下载完成后直接从网页下载文件
- 中文界面，深色主题，移动端适配

## 快速开始

### Docker Compose（推荐）

```bash
cd web-nas
docker compose up -d --build
```

然后浏览器访问：

```
http://你的NAS地址:7860
```

### Docker 手动构建

```bash
cd web-nas
docker build -t m3u8-downloader .
docker run -d \
  --name m3u8-downloader \
  -p 7860:7860 \
  -v /你的下载目录:/downloads \
  m3u8-downloader
```

### 本地运行（开发用）

```bash
cd web-nas
pip install -r requirements.txt
python app.py
```

需要系统已安装 ffmpeg。

## 字段说明

| 字段 | 说明 |
|------|------|
| M3U8 链接 | `.m3u8` 地址，必填 |
| 文件名 | 输出文件名，默认 `video.mp4` |
| Referer | 部分网站需要填写来源页面地址 |
| Cookie | 需要登录的站点，从浏览器复制 Cookie |
| 画质 | 点击「探测画质」后选择 |

## 目录结构

```
web-nas/
├── app.py              # 后端主程序
├── Dockerfile          # Docker 构建文件
├── docker-compose.yml  # Compose 配置
├── requirements.txt    # Python 依赖
├── README.md           # 本文件
├── static/
│   ├── app.js          # 前端逻辑
│   └── styles.css      # 样式
└── templates/
    └── index.html      # 页面模板
```

## 配置项

通过环境变量配置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DOWNLOAD_DIR` | `/downloads` | 下载文件保存目录 |
| `PORT` | `7860` | 服务端口 |
| `FFMPEG_PATH` | 自动检测 | ffmpeg 可执行文件路径 |

## 注意事项

- 下载的视频保存在宿主机挂载的 `./downloads` 目录中
- 部分加密、DRM 保护或需要特殊鉴权的视频可能无法下载
- 请只下载你有权保存的视频内容
