# 🛡️ Dota Watchlist — Player Tracker + Telegram Alerts

ระบบติดตามผู้เล่น Dota 2 ที่ต้องการ และแจ้งเตือนผ่าน **Telegram** เมื่อเจอผู้เล่นในแมทช์

## 🖥️ แอป DotaNotify.exe (แนะนำ)

แอปเดียวสำหรับจัดการ Watchlist, เช็คแมทช์อัตโนมัติ และรับแจ้งเตือน Telegram

1. ดาวน์โหลด `DotaNotify.exe` จาก GitHub Actions artifact หรือหน้า Releases
2. ในแท็บ **ตั้งค่า** กรอก Steam32 Account ID, Telegram Bot Token, Telegram Chat ID และ Steam Web API Key (วิธีสร้าง bot และหา Chat ID ดูหัวข้อ "สร้าง Telegram Bot และขอ Token" ด้านล่าง) แล้วกด **ทดสอบ** เพื่อส่งข้อความทดสอบเข้า Telegram
3. กด **บันทึก** — โปรแกรมจะเริ่มทำงานอัตโนมัติเมื่อมี Account ID
4. เพิ่มผู้เล่นที่ต้องการติดตามในแท็บ **Watchlist**

สามารถระบุ Steam32 Account ID ได้หลายบัญชี โดยคั่นด้วยจุลภาค เช่น `12345678, 87654321`

ข้อมูลของแอปเก็บแยกจากไฟล์โปรแกรม: Windows ที่ `%APPDATA%\DotaNotify` และ Linux/macOS ที่ `~/.dotanotify`

สร้างไฟล์ `.exe` เองบน Windows:

```bash
pip install -r requirements.txt -r requirements-dev.txt
pyinstaller --noconfirm --onefile --windowed --name DotaNotify DotaNotify.py
```

### แจ้งเตือนทันทีระหว่าง Draft

การแจ้งเตือนทันทีต้องใช้ **Steam Web API Key** จาก `steamcommunity.com/dev/apikey` (Domain: `localhost`) และใส่ `-condebug` ใน Launch Options ของ Dota 2 ที่ Steam > Dota 2 > Properties โปรแกรมจะเริ่มตรวจสอบพร้อมกับตัวเช็คแมทช์เมื่อเปิดใช้งาน ข้อมูลผู้เล่นจาก Steam อาจล่าช้าระหว่าง Draft

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
| `TELEGRAM_BOT_TOKEN` | Bot Token จาก @BotFather |
| `TELEGRAM_CHAT_ID` | Chat ID ที่จะรับแจ้งเตือน |

#### หา Steam32 Account ID
1. เปิด OpenDota: `https://www.opendota.com/players/[SteamID64]`  
2. ดูที่ URL — ตัวเลขหลัง `/players/` คือ Account ID

#### สร้าง Telegram Bot และขอ Token
1. เปิด Telegram แล้วค้นหา `@BotFather`
2. ส่งคำสั่ง `/newbot` → ตั้งชื่อและ username ของ bot (ต้องลงท้ายด้วย `bot`)
3. BotFather จะส่ง **Token** กลับมา (หน้าตาเช่น `123456789:ABC-DEF...`) — เก็บเป็นความลับ

#### หา Chat ID
ไม่ว่าวิธีไหน ต้องเปิดแชทกับ bot ที่เพิ่งสร้างแล้วกด **Start** ก่อน (bot จะส่งหาคุณไม่ได้ถ้ายังไม่เคยคุยกัน)

**ทางลัด (แชทส่วนตัว):** ส่งข้อความหา `@userinfobot` — เลข `Id` ที่ตอบกลับมาคือ Chat ID ของคุณ (ใช้ได้แม้ bot ตั้ง webhook ไว้ ซึ่งกรณีนั้น `getUpdates` ด้านล่างจะ error 409)

**หรือผ่าน `getUpdates`** (ใช้กับกลุ่มได้ด้วย):
1. ถ้าต้องการรับแจ้งเตือนในกลุ่ม: เพิ่ม bot เข้ากลุ่ม แล้วส่งข้อความในกลุ่ม
2. เปิด `https://api.telegram.org/bot<TOKEN>/getUpdates` ในเบราว์เซอร์ (แทน `<TOKEN>` ด้วย Token ของคุณ)
3. หา `"chat":{"id": ...}` — ตัวเลขนั้นคือ Chat ID (กลุ่มจะเป็นเลขติดลบ เช่น `-1001234567890`)

#### ตั้งค่าตอนรันสคริปต์ในเครื่อง (`check_dota.py` / `live_check.py`)
ใส่ค่าในไฟล์ `.env` (ไฟล์นี้ถูก ignore ไม่ขึ้น git):
```
MY_DOTA_ID=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

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

## 📱 ตัวอย่าง Telegram Notification

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
