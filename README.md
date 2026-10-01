# Copilot Gateway (`gpt-5.4-nano`)

Copilot Gateway adalah reverse proxy API ringan berbasis FastAPI yang meneruskan permintaan berformat OpenAI API ke GitHub Copilot API khusus untuk mengakses model **`gpt-5.4-nano`**.

Gateway ini menggunakan otentikasi **GitHub OAuth Device Flow**, sehingga Anda dapat menggunakan akun GitHub (seperti **GitHub Student Developer Pack** atau GitHub Copilot biasa) **tanpa perlu API Key Copilot berbayar**.

Gateway ini siap dihubungkan sebagai **Custom Provider** pada platform gateway/router LLM seperti **[OmniRoute](https://github.com/diegosouzapw/OmniRoute)**, baik secara lokal melalui **Custom Docker Network** maupun diakses dari internet menggunakan **Cloudflare Tunnel**.

Repository Link: `github.com/moccalatte/copilot-gateway`

---

## 🚀 Fitur Utama

- **Model Focus**: Khusus meneruskan request ke model **`gpt-5.4-nano`** GitHub Copilot.
- **GitHub Student / OAuth Flow**: Cukup login dengan akun GitHub Copilot Anda via OAuth device code, tanpa memerlukan API key Copilot.
- **Persistent Token Storage**: Token hasil OAuth tersimpan di direktori `./data/tokens.json` secara persisten (aman saat container direstart).
- **Custom Docker Network**: Dilengkapi kustom jaringan Docker (`copilot-net`) agar container OmniRoute atau service lain dapat terhubung langsung antar-container.
- **OmniRoute Compatible**: Kompatibel penuh dengan endpoint OpenAI (`/v1/chat/completions` dan `/v1/models`).
- **Cloudflare Tunnel Friendly**: Panduan lengkap ekspos endpoint publik dengan SSL gratis via Cloudflare Tunnel (`https://copilot.domainkamu.com/v1`).
- **Docker & Docker Compose Ready**: Dilengkapi Dockerfile dan docker-compose.yml yang mudah dijalankan.

---

## 📋 Persyaratan Dasar

Sebelum memulai, pastikan Anda memiliki:
1. Akun GitHub yang sudah memiliki akses **GitHub Copilot** (termasuk akun **GitHub Student Developer Pack**).
2. Perangkat/Server (VPS / Laptop) yang terinstal:
   - **Git**
   - **Docker** dan **Docker Compose** *(Sangat direkomendasikan untuk pemula)*

---

## 🔰 Panduan Langkah demi Langkah (Untuk Pemula)

### Langkah 1: Clone Repository ini

Buka Terminal / Command Prompt / SSH VPS Anda, lalu jalankan perintah berikut:

```bash
git clone https://github.com/moccalatte/copilot-gateway.git
cd copilot-gateway
```

---

### Langkah 2: Konfigurasi File Environment `.env`

Salin file contoh `env.example` menjadi `.env`:

```bash
cp env.example .env
```

Buka file `.env` (misal dengan `nano .env`) dan ubah `GATEWAY_API_KEY` menjadi kunci rahasia buatan Anda sendiri. Kunci ini nanti digunakan untuk mengamankan akses ke Gateway Anda:

```env
GITHUB_CLIENT_ID=Iv1.b507a08c87ecfe98
GATEWAY_API_KEY=kunci-rahasia-pilihan-anda-123
HOST=0.0.0.0
PORT=8787
DATA_DIR=data
```

---

### Langkah 3: Jalankan Service dengan Docker Compose (Custom Network)

Jalankan perintah berikut di dalam folder `copilot-gateway`:

```bash
docker-compose up -d
```

*Perintah di atas akan membuat kustom network `copilot-net`, mendownload, dan menjalankan Copilot Gateway secara background.*

---

### Langkah 4: Login dengan Akun GitHub Copilot / Student Anda

Gateway memerlukan izin dari akun GitHub Anda satu kali saja:

1. Jalankan perintah login Docker:
   ```bash
   docker exec -it copilot-gateway python server.py login
   ```

2. Terminal akan menampilkan tautan verifikasi dan kode unik, contoh:
   ```text
   Open: https://github.com/login/device
   Code: ABCD-1234
   ```

3. Buka browser Anda, kunjungi `https://github.com/login/device`, pastikan Anda sudah masuk (login) ke akun GitHub Copilot/Student Anda.
4. Masukkan kode yang tertera di terminal (misal `ABCD-1234`) lalu klik **Authorize**.
5. Setelah berhasil, di terminal akan muncul tulisan **`GitHub login OK.`**.

*Token login Anda sekarang tersimpan aman di folder `./data/tokens.json`. Anda **tidak perlu login ulang** meskipun server atau Docker di-restart.*

---

## 🌐 Panduan Custom Docker Network dengan OmniRoute

Docker Compose di repository ini otomatis membuat kustom bridge network bernama **`copilot-net`**.

Jika **OmniRoute** Anda dijalankan di dalam Docker di server yang sama, Anda cukup menghubungkan container OmniRoute Anda ke network `copilot-net` ini agar keduanya bisa saling berkomunikasi secara langsung tanpa perlu lewat IP publik.

### Cara Menghubungkan Container OmniRoute ke Network `copilot-net`:

Jalankan perintah berikut di terminal server Anda (ganti `omniroute` dengan nama container OmniRoute Anda):

```bash
docker network connect copilot-net omniroute
```

Setelah terhubung, OmniRoute dapat memanggil Copilot Gateway menggunakan hostname internal Docker:
`http://copilot-gateway:8787/v1`

---

## 🌐 Panduan Ekspos ke Internet via Cloudflare Tunnel (Opsional)

Jika Anda ingin mengakses Gateway ini dari luar (misal dari VPS OmniRoute di server berbeda) menggunakan domain Anda sendiri (contoh: `https://copilot.domainkamu.com/v1`), gunakan **Cloudflare Tunnel**.

### A. Persiapan di Cloudflare Zero Trust Dashboard

1. Buka [Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com/).
2. Di menu sebelah kiri, pilih **Networks** -> **Tunnels**.
3. Klik tombol **Add a tunnel** -> Pilih **Cloudflared** -> Klik **Next**.
4. Beri nama tunnel Anda (contoh: `copilot-gateway-tunnel`) lalu klik **Save tunnel**.
5. Pilih **Docker** untuk mendapatkan token tunnel. Simpan token ini.

### B. Menambahkan Public Hostname di Dashboard Cloudflare

1. Klik tab **Public Hostname** -> klik **Add a public hostname**.
2. Isi form konfigurasi:
   - **Subdomain**: `copilot` (atau nama lain)
   - **Domain**: Pilih domain Anda (misal: `domainkamu.com`)
   - **Service Type**: `HTTP`
   - **URL**: `copilot-gateway:8787`
3. Klik **Save hostname**.

### C. Menjalankan Cloudflared Container

Di file `docker-compose.yml`, service `cloudflared` sudah disiapkan pada network `copilot-net`. Isi variabel `CLOUDFLARE_TUNNEL_TOKEN` di `.env` Anda atau jalankan langsung:

```bash
docker-compose --profile cloudflare up -d
```

Sekarang Gateway Anda dapat diakses dari internet di tautan HTTPS yang aman:
`https://copilot.domainkamu.com/v1`

---

## 🔗 Panduan Hubungkan ke OmniRoute (github.com/diegosouzapw/OmniRoute)

Untuk menghubungkan Copilot Gateway ini ke **OmniRoute**:

1. Buka Dashboard **OmniRoute** Anda.
2. Masuk ke menu **Providers** -> **Custom Providers** -> **Add Custom Provider**.
3. Pilih tipe Provider: **OpenAI Compatible**.
4. Isi form berikut:
   - **Provider Name**: `Copilot Gateway`
   - **Base URL**:
     - Jika menggunakan Custom Docker Network (`copilot-net`):
       `http://copilot-gateway:8787/v1`
     - Jika menggunakan Cloudflare Tunnel:
       `https://copilot.domainkamu.com/v1`
     - Jika berjalan di Host/Localhost yang sama:
       `http://localhost:8787/v1` (atau `http://host.docker.internal:8787/v1`)
   - **API Key**: Isi sesuai `GATEWAY_API_KEY` di file `.env` Anda (misal `kunci-rahasia-pilihan-anda-123`).
   - **Models**: Tambahkan model **`gpt-5.4-nano`**.
5. Klik **Save / Submit**.
6. Selesai! Model `gpt-5.4-nano` sekarang siap digunakan melalui OmniRoute.

---

## 📡 API Endpoints Reference

### 1. Health Check
- **GET** `/health`
- Contoh Response:
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
- Header: `Authorization: Bearer <GATEWAY_API_KEY>`

### 3. Chat Completions
- **POST** `/v1/chat/completions` atau `/v1/responses`
- Header: `Authorization: Bearer <GATEWAY_API_KEY>`
- Contoh pengujian dengan `curl`:
  ```bash
  curl https://copilot.domainkamu.com/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer kunci-rahasia-pilihan-anda-123" \
    -d '{
      "messages": [{"role": "user", "content": "Halo, tes gpt-5.4-nano!"}],
      "stream": false
    }'
  ```

---

## ⚙️ Variabel Lingkungan (Environment Variables)

| Variable | Deskripsi | Default |
| --- | --- | --- |
| `GATEWAY_API_KEY` | Key rahasia pengaman akses API Gateway | `change-this-to-a-long-random-secret` |
| `HOST` | Bind IP Host server | `0.0.0.0` |
| `PORT` | Bind Port server | `8787` |
| `DATA_DIR` | Folder tempat penyimpanan file token OAuth | `data` |
| `GITHUB_CLIENT_ID` | Client ID OAuth GitHub Device Code Flow | `Iv1.b507a08c87ecfe98` |
| `CLOUDFLARE_TUNNEL_TOKEN` | Token Tunnel dari Cloudflare Zero Trust (Opsional) | `""` |

---

## 🧪 Cara Pengujian Unit Test

Jika Anda ingin menjalankan pengujian internal:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. pytest
```

---

## 📄 License

MIT License.
