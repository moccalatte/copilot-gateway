# Copilot Gateway (`gpt-5.4-nano`)

Copilot Gateway adalah reverse proxy API ringan berbasis FastAPI yang Meneruskan permintaan OpenAI API ke GitHub Copilot API untuk mengakses model **`gpt-5.4-nano`**.

Gateway ini menggunakan otentikasi **GitHub OAuth Device Flow**, sehingga Anda dapat menggunakan akun GitHub (seperti **GitHub Student Developer Pack** atau GitHub Copilot biasa) **tanpa perlu API Key Copilot berbayar**.

Gateway ini siap dihubungkan sebagai **Custom Provider** pada platform gateway/router LLM seperti **[OmniRoute](https://github.com/diegosouzapw/OmniRoute)**.

Repository Link: `github.com/moccalatte/copilot-gateway`

---

## 🚀 Fitur Utama

- **Model Focus**: Khusus meneruskan request ke model **`gpt-5.4-nano`** GitHub Copilot.
- **GitHub Student / OAuth Flow**: Cukup login dengan akun GitHub Copilot Anda via OAuth device code, tanpa memerlukan API key Copilot.
- **Persistent Token Storage**: Token hasil OAuth tersimpan di direktori `./data/tokens.json` secara persisten (aman saat container direstart).
- **OmniRoute Integration**: Kompatibel penuh dengan endpoint OpenAI (`/v1/chat/completions` dan `/v1/models`).
- **Docker & Docker Compose Ready**: Dilengkapi Dockerfile dan docker-compose.yml.
- **CORS Enabled**: Mendukung panggilan lintas origin dari frontend OmniRoute.

---

## 🛠️ Persyaratan

- Akun GitHub dengan akses GitHub Copilot aktif (misal: **GitHub Student**).
- **Docker & Docker Compose** (rekomendasi untuk deployment persisten) ATAU **Python 3.10+**.

---

## ⚡ Cara Menjalankan dengan Docker (Rekomendasi)

### 1. Jalankan Container
Clone repo dan jalankan Docker Compose:

```bash
docker-compose up -d
```

### 2. Login dengan Akun GitHub Copilot / Student Anda
Gateway memerlukan otentikasi OAuth satu kali ke akun GitHub Anda:

```bash
docker exec -it copilot-gateway python server.py login
```

Langkah login:
1. Terminal akan menampilkan tautan verifikasi (misal: `https://github.com/login/device`) dan sebuah kode unik (misal: `ABCD-1234`).
2. Buka tautan tersebut di browser, pastikan Anda masuk dengan akun GitHub Copilot (misal: akun GitHub Student Anda), lalu masukkan kodenya.
3. Setelah mendapat konfirmasi "GitHub login OK.", token OAuth akan tersimpan secara otomatis di `./data/tokens.json`.
4. Token tersimpan pada volume Docker persisten sehingga **tidak perlu login ulang** ketika container di-restart atau di-stop.

---

## 🐍 Cara Menjalankan Lokal (Python Venv)

### 1. Instalasi
```bash
python3 -m venv .venv
source .venv/bin/activate  # Linux / macOS
# .venv\Scripts\activate   # Windows

pip install -r requirements.txt
```

### 2. Konfigurasi `.env` (Opsional)
```bash
cp env.example .env
```
Anda dapat menyesuaikan `GATEWAY_API_KEY` pada file `.env` sebagai Kunci Rahasia untuk mengakses Gateway ini.

### 3. Login GitHub OAuth
```bash
python server.py login
```

### 4. Jalankan Server
```bash
python server.py
```
Server berjalan pada `http://localhost:8787`.

---

## 🔗 Panduan Hubungkan ke OmniRoute (github.com/diegosouzapw/OmniRoute)

[OmniRoute](https://github.com/diegosouzapw/OmniRoute) dapat menggunakan Copilot Gateway ini sebagai Custom Provider:

1. Buka dashboard **OmniRoute** Anda.
2. Navigasi ke **Providers** -> **Custom Providers** -> **Add Custom Provider**.
3. Pilih Provider Type: **OpenAI Compatible**.
4. Isi data konfigurasi berikut:
   - **Provider Name**: `Copilot Gateway` (atau nama lain)
   - **Base URL**:
     - Jika OmniRoute & Copilot Gateway dalam 1 network Docker: `http://copilot-gateway:8787/v1`
     - Jika berjalan di localhost server yang sama: `http://localhost:8787/v1` (atau `http://host.docker.internal:8787/v1`)
   - **API Key**: Isi sesuai `GATEWAY_API_KEY` yang diatur pada file `.env` Gateway ini (default: `change-this-to-a-long-random-secret`).
   - **Models**: Tambahkan model `gpt-5.4-nano`.
5. Klik **Save**.
6. Sekarang Anda dapat menggunakan model `gpt-5.4-nano` melalui OmniRoute!

---

## 📡 API Endpoints

### 1. Health Check
- **GET** `/health`
- **Response**:
  ```json
  {
    "ok": true,
    "provider": "github-copilot",
    "authenticated": true,
    "model": "gpt-5.4-nano"
  }
  ```

### 2. List Models
- **GET** `/v1/models`
- **Header**: `Authorization: Bearer <GATEWAY_API_KEY>`

### 3. Chat Completions
- **POST** `/v1/chat/completions` atau `/v1/responses`
- **Header**: `Authorization: Bearer <GATEWAY_API_KEY>`
- **Example Request**:
  ```bash
  curl http://localhost:8787/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer change-this-to-a-long-random-secret" \
    -d '{
      "messages": [{"role": "user", "content": "Halo gpt-5.4-nano!"}],
      "stream": false
    }'
  ```

---

## ⚙️ Environment Variables

| Variable | Description | Default |
| --- | --- | --- |
| `GATEWAY_API_KEY` | Key rahasia untuk mengamankan akses ke Gateway | `change-this-to-a-long-random-secret` |
| `HOST` | IP Host server | `0.0.0.0` |
| `PORT` | Port server | `8787` |
| `DATA_DIR` | Folder penyimpanan token OAuth | `data` |
| `GITHUB_CLIENT_ID` | Client ID GitHub OAuth Device Flow | `Iv1.b507a08c87ecfe98` |

---

## 🧪 Testing

Jalankan test suite dengan pytest:

```bash
PYTHONPATH=. .venv/bin/pytest
```

---

## 📄 License

MIT License.
