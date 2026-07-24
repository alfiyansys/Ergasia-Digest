# PLAN.md — Ergasia Digest

Webservice kecil untuk menarik activity dari GitHub dan GitLab.com, menggabungkannya jadi satu summary, lalu mengirimkannya ke chat (Slack / OpenClaw). Dijalankan sebagai HTTP API (FastAPI) yang dipicu oleh cron via `curl`.

## 1. Struktur Project

```
ergasia-digest/
├── app.py              # FastAPI app + endpoints (entrypoint)
├── digest.py           # Core logic: fetch → aggregate → format
├── sources/
│   ├── github_source.py    # GitHub Events API client
│   └── gitlab_source.py    # GitLab.com Events API client
├── state.py             # Tracks last-run timestamp (per-source)
├── notify.py            # Kirim hasil digest (Slack / generic webhook / stdout)
├── requirements.txt
├── .env.example
└── README.md             # Cara install, konfigurasi, deploy, cron
```

**Status saat ini:** `sources/`, `state.py`, dan `notify.py` sudah ada dan sudah lolos smoke test (logic `build_digest` berjalan). `digest.py` masih versi CLI lama dan perlu di-strip jadi pure function. `app.py` belum dibuat.

## 2. Endpoints (Rencana)

| Method | Path               | Fungsi |
|--------|--------------------|--------|
| GET    | `/health`          | Cek service hidup. |
| GET    | `/digest/preview`  | Fetch + build digest, **return sebagai JSON/text** — tanpa update state, tanpa kirim ke notify target. Untuk cek manual. |
| POST   | `/digest/run`      | Fetch + build + **kirim ke notify target** (Slack/OpenClaw) + update state (`last_run`). Endpoint ini yang dipanggil cron. |
| GET    | `/digest/latest`   | Return digest terakhir yang berhasil dikirim (di-cache di memori/file). |

**Auth:** endpoint diproteksi dengan shared secret sederhana (header `X-API-Key`), dicek terhadap env var `API_KEY`. Cukup untuk internal tool — tidak perlu OAuth.

## 3. Konfigurasi (`.env`)

Sama seperti sebelumnya, ditambah:
- `API_KEY` — shared secret untuk proteksi endpoint.
- `PORT` — default `8000`.

## 4. Alur Cron

```
0 8 * * * curl -s -X POST -H "X-API-Key: $ERGASIA_KEY" http://127.0.0.1:8000/digest/run
```

Service berjalan terus-menerus (systemd unit / uvicorn); cron hanya men-trigger via HTTP sesuai jadwal (misalnya tiap pagi). Pendekatan ini lebih fleksibel dibanding script one-shot: bisa di-trigger manual (`/digest/preview`) kapan saja tanpa menunggu cron, dan mudah dipanggil langsung dari OpenClaw sebagai tool/endpoint.

## 5. Deployment

- Dijalankan via `uvicorn app:app --host 127.0.0.1 --port 8000` di belakang systemd service, mengikuti pola service lain di `invis`.
- Tidak perlu expose ke luar — cukup localhost, dengan reverse proxy Nginx opsional kalau perlu diakses dari luar `invis`.

## 6. Langkah Selanjutnya

1. Refactor `digest.py`: pisahkan `fetch_all_events()` dan `build_digest()` menjadi pure function (sudah ada, tinggal rapikan import dan hapus bagian `if __name__ == "__main__"`).
2. Tulis `app.py` — FastAPI app dengan 4 endpoint di atas plus API key middleware.
3. Update `state.py` bila perlu, untuk menyimpan juga cache "last digest text" bagi endpoint `/digest/latest`.
4. Update `requirements.txt` (+`fastapi`, +`uvicorn`).
5. Tulis `README.md` — cara install, isi `.env`, jalankan lokal, setup systemd + cron.
6. *(Opsional)* Tambahkan endpoint `/digest/run` versi GitLab self-hosted, untuk dipakai GitLab CSRG bila diperlukan nanti.

Kalau plan ini disetujui, lanjut eksekusi langkah 1–5.
