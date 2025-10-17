<p align="center">
  <img src="./app/mini_icon.png" alt="Mini logo" width="256" height="256">
</p>
<h1 align="center">Mini Image and File Server</h1>
<p align="center">
  <img src="https://i.imgur.com/FbF2edB.png" alt="Mini Image and File Server" width="800"/>
</p>

<p align="center">
  <em>Small image and file server to quickly share screenshots/files with your friends!</em>
</p>

<p align="center">
  Supports most image types, and compressed file types. Has auto deletion, a small webpage and can accept uploads over CLI. <br>
  Perfect for use with screenshot software with custom script implementation.
</p>



## Installing / Getting Started

### **Docker**

```bash
docker run -d --name mini-image-file-server \
  -e ALLOWED_HOSTS=IP or Domain of your Host \
  -e PORT=8080 \
  -p 8080:8080 \
  -v path_to_your_persistant_data_or_volume:/app/data \
  strikzz/mini-image-file-server:latest
```

### **Docker Compose (Recommended)**

```yaml
services:
  mini-image-file-server:
    image: strikzz/mini-image-file-server:latest
    container_name: mini-image-file-server
    restart: unless-stopped
    environment:
      - DATA_ROOT=
      - TTL_DAYS=
      - CLEANUP_INTERVAL_SECONDS=
      - MAX_FILE_MB= 
      - LANDINGPAGE_TITLE=
      - ALLOWED_HOSTS=IP or Domain of your Host
      - PORT=8080
    volumes:
      - Path_to_your_persistant_data:/app/data
    expose:
      - "8080"
```
> For a full example, see [docker/docker-compose.example.yaml](./docker/docker-compose.example.yaml)

---

## Configuration (Environment Variables)

| Variable             | Description                                                           | Default         |
|----------------------|-----------------------------------------------------------------------|-----------------|
| DATA_ROOT            | Location path of the "data" folder relative to main.py.    | `data`     |
| TTL_DAYS             | Lifetime of files until they are deleted in days                                      | `14`          |
| CLEANUP_INTERVAL_SECONDS      | Run interval of the deletion checker                             | `21600`       |
| MAX_FILE_MB          | Maximum file size in MB                   | `15`           |
| LANDINGPAGE_TITLE        | The header title that is displayed on the landingpage              | `Mini image and file server`          |
| ALLOWED_HOSTS             | List of allowed Hosts. You need to input your domain/IP here                                              | `localhost, 127.0.0.1`          |
| PORT            | Which port the service will be hosted on      | `8080`       |

---

## Usage

Besides using the built-in web interface to upload and manage files,  
you can also interact with the server over CLI — for example using `curl`.

### Uploading a file via `curl`

The `/upload` endpoint accepts a single file through a multipart form request:

```bash
curl -X POST https://<your-server>/upload -F "file=@example.png"
```

This will return a JSON response containing metadata and access URLs, for example:
```json
{
  "type": "image",
  "id": "a9d8b4e7c3f24e0f8b6a1d9f6a34bcd1",
  "page_url": "https://<your-server>/i/a9d8b4e7c3f24e0f8b6a1d9f6a34bcd1",
  "raw_url": "https://<your-server>/raw/image/a9d8b4e7c3f24e0f8b6a1d9f6a34bcd1"
}
```

You can also retrieve JSON listings of existing uploads:
```bash
curl https://<your-server>/list/images
curl https://<your-server>/list/files
```
---

## Developing

### **VS Code Dev Container (for quick start)**

The repo includes a full `.devcontainer` setup for Visual Studio Code.  

**Quick Start:**
1. Open the repo in VS Code and follow the Dev Container prompts (reopen in container).
2. This should start the whole dev stack. If it doesn't start automatically, run `docker-compose` up manually.
3. Adjust Compose environment as needed.

---

## ✨ Features

- ⚡ Lightweight **FastAPI-based file and image server** ...
- 🧩 Supports **image + archive uploads** (JPG, PNG, ZIP, RAR, ...)
- 🧹 Automatic **file expiration & cleanup**
- 🔐 Designed for use **behind Auth/Reverse Proxies**
- 💾 Persistent storage with **Docker volume mounting**

---

## Contributing

**Contributions are welcome!**  
Please fork the repo and use feature branches for your changes.  
Pull requests are highly appreciated!

---

## Links

- **Repository:** [https://github.com/StrikzZ/mini-image-file-server](https://github.com/StrikzZ/mini-image-file-server)
- **Releases:** [https://github.com/StrikzZ/mini-image-file-server/releases](https://github.com/StrikzZ/mini-image-file-server/releases)
- **Issue tracker:** [https://github.com/StrikzZ/mini-image-file-server/issues](https://github.com/StrikzZ/mini-image-file-server/issues)
- **DockerHub Image:** [https://hub.docker.com/r/strikzz/mini-image-file-server](https://hub.docker.com/r/strikzz/mini-image-file-server)

---

## Licensing

This project is licensed under the **MIT License**.  
See the [LICENSE](LICENSE) file for details.

---
