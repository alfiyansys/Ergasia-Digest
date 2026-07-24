# PLAN.md — Ergasia Digest

Webservice kecil buat narik activity dari GitHub + GitLab.com, gabungin jadi satu summary, dan kirim ke chat (Slack / OpenClaw). Dijalankan sebagai HTTP API (FastAPI), dipicu oleh cron via `curl`.

## 1. Struktur project

```
ergasia-digest/
├── app.py                 # FastAPI app + endpoints (entrypoint)
├── digest.py               # Core logic: fetch → aggregate → format
├── sources/
│   ├── github_source.py    # GitHub Events API client
│   └── gitlab_source.py    # GitLab.com Events API client
├── state.py                 # Tracks last-run timestamp (per-source)
├── notify.py                 # Kirim hasil digest (slack / generic webhook / stdout)
├── requirements.txt
├── .env.example
└── README.md                 # Cara install, konfigurasi, deploy, cron
```

Status sekarang: `sources/`, `state.py`, `notify.py` sudah ada dan sudah di-smoke-test (logic `build_digest` jalan). `digest.py` masih versi CLI lama, perlu di-strip jadi pure functions. `app.py` belum dibuat.

## 2. Endpoints (rencana)

| Method | Path              | Fungsi                                                                 |
|--------|-------------------|-------------------------------------------------------------------------|
| GET    | `/health`         | Cek service hidup                                                       |
| GET    | `/digest/preview` | Fetch + build digest, **return sebagai JSON/text** — tanpa update state, tanpa kirim ke notify target. Buat cek manual. |
| POST   | `/digest/run`     | Fetch + build + **kirim ke notify target** (Slack/OpenClaw) + update state (`last_run`). Ini yang dipanggil cron. |
| GET    | `/digest/latest`  | Return digest terakhir yang berhasil dikirim (di-cache di memori/file) |

Auth: endpoint di-protect pakai shared secret sederhana (header `X-API-Key`), dicek terhadap env var `API_KEY` — cukup buat internal tool, ga perlu OAuth.

## 3. Konfigurasi (.env)

Sama seperti sebelumnya, plus:
- `API_KEY` — shared secret buat proteksi endpoint
- `PORT` — default 8000

## 4. Alur cron

```
0 8 * * * curl -s -X POST -H "X-API-Key: $ERGASIA_KEY" http://127.0.0.1:8000/digest/run
```

Service jalan terus (systemd unit / uvicorn), cron cuma nge-trigger via HTTP tiap jadwal (misal tiap pagi). Ini lebih fleksibel dari script one-shot karena bisa juga di-trigger manual (`/digest/preview`) kapan aja tanpa nunggu cron, dan gampang dipanggil dari OpenClaw langsung sebagai tool/endpoint.

## 5. Deployment

- Jalanin via `uvicorn app:app --host 127.0.0.1 --port 8000` di balik systemd service, mirip pola service lain di `invis`.
- Nggak perlu expose ke luar — cukup localhost + reverse proxy Nginx kalau mau diakses dari luar `invis`.

## 6. Langkah selanjutnya

1. Refactor `digest.py`: pisah `fetch_all_events()` dan `build_digest()` jadi pure function (sudah ada, tinggal rapikan import & hilangkan bagian `if __name__ == "__main__"`).
2. Tulis `app.py` — FastAPI app dengan 4 endpoint di atas + API key middleware.
3. Update `state.py` kalau perlu simpan juga cache "last digest text" buat endpoint `/digest/latest`.
4. Update `requirements.txt` (+`fastapi`, `+uvicorn`).
5. Tulis `README.md` — cara install, isi `.env`, jalanin lokal, setup systemd + cron.
6. (Opsional) Tambah endpoint `/digest/run` versi GitLab self-hosted kalau nanti mau dipakai buat GitLab CSRG juga.

Kalau plan ini oke, saya lanjut eksekusi langkah 1–5.
