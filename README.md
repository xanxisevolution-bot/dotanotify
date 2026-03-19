# 🛡️ Dota Watchlist — Player Tracker + Line Alerts

ระบบติดตามผู้เล่น Dota 2 ที่ต้องการ และแจ้งเตือนผ่าน **Line Notify** เมื่อเจอผู้เล่นในแมทช์

---

## 📁 โครงสร้างไฟล์

```
dota-watchlist/
├── .github/
│   └── workflows/
│       └── dota_check.yml       ← GitHub Actions (รันทุก 30 นาที)
├── check_dota.py                ← Script หลัก
├── watchlist.json               ← รายชื่อผู้เล่นที่ติดตาม
├── state.json                   ← จำ match_id ล่าสุดที่เช็คแล้ว
├── heroes_cache.json            ← Cache ชื่อ Hero (auto-generated)
├── watchlist_manager.html       ← เครื่องมือจัดการ Watchlist (เปิดใน Browser)
└── README.md
```

---

## 🚀 วิธีติดตั้ง

### 1. สร้าง GitHub Repository
```bash
git init dota-watchlist
cd dota-watchlist
# copy ไฟล์ทั้งหมดเข้ามา
git add .
git commit -m "init"
git push origin main
```

### 2. ตั้งค่า GitHub Secrets

ไปที่ **Settings → Secrets and variables → Actions** แล้วเพิ่ม:

| Secret | ค่า |
|--------|-----|
| `MY_DOTA_ID` | Steam32 Account ID ของคุณ |
| `LINE_TOKEN` | Line Notify Token |

#### หา Steam32 Account ID
1. เปิด OpenDota: `https://www.opendota.com/players/[SteamID64]`  
2. ดูที่ URL — ตัวเลขหลัง `/players/` คือ Account ID

#### ขอ Line Notify Token
1. ไปที่ `https://notify-bot.line.me/`
2. Login → My page → Generate token
3. เลือก Chat หรือ Group ที่ต้องการรับแจ้งเตือน

---

## 📋 การจัดการ Watchlist

### เปิด watchlist_manager.html ในเบราว์เซอร์

- **Tab "เพิ่มผู้เล่น"** — ค้นหาผู้เล่นด้วย Account ID จาก OpenDota แล้วเพิ่มพร้อม Note
- **Tab "Watchlist"** — ดู/แก้ไข/ลบผู้เล่น
- **Tab "Import/Export"** — Export เป็น JSON แล้วนำไป commit ลง GitHub

### ขั้นตอนหลังเพิ่มผู้เล่น
1. Export JSON ใน watchlist_manager.html
2. แทนที่ไฟล์ `watchlist.json` ใน repo
3. Commit & Push

---

## 📱 ตัวอย่าง Line Notification

```
🎮 พบผู้เล่นใน Watchlist!
──────────────────
👤 PlayerName
🏷️ แท็ก: เล่นดี
📝 Note: Invoker เก่งมาก เล่นด้วยทุกครั้ง
──────────────────
🕹️ สถานะ: เพื่อนร่วมทีม 🤝
🦸 Hero: Invoker  (12/2/8)
🏆 ผล: ชนะ ✅
🔗 Match ID: 7912345678
⏰ 19/03/2026 14:30 UTC
🌐 dotabuff.com/matches/7912345678
```

---

## ⚙️ ปรับแต่ง

เปิดไฟล์ `check_dota.py` แก้ได้ที่บรรทัดบน:
```python
MATCH_LIMIT = 20   # จำนวน match ที่ดึงมาตรวจสอบต่อครั้ง
```

ความถี่การรัน แก้ใน `.github/workflows/dota_check.yml`:
```yaml
- cron: '*/30 * * * *'   # ทุก 30 นาที (ขั้นต่ำของ GitHub = 5 นาที)
```
