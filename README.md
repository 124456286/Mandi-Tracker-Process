# Mandi Purchase Tracker — All Features Edition

A local Windows-friendly Flask + SQLite mandi business system for purchase tracking, grain averages, analytics, stock, sales and reports.

## Included features

- Premium agricultural dashboard
- Today / monthly purchase statistics
- Wheat average — all varieties combined
- Mustard average
- Paddy average — all varieties combined
- Methi Seeds average — all varieties combined
- Moong Total / ₹3,000+ / Below ₹3,000 averages
- Daily purchase averages with total amount
- Daily grain + variety analysis and Excel export
- Multi-file / multi-sheet dynamic Excel import
- Hindi + English column detection
- Duplicate import protection
- Advanced purchase search and filters (grain, mandi, dates, rate, seller/variety/notes)
- Manual purchase entry
- Mandi-wise analysis
- Price / quantity trend charts
- Seller/farmer field and searchable records
- Complete purchase cost fields: mandi fee, nirashrit fee, transport, labour/hamali, other cost
- True cost per quintal analysis
- Sales register
- Inventory / stock calculation
- Estimated profit/loss
- Excel reports
- PDF purchase report
- ZIP backup and active-profile restore
- Automatic Excel-folder sync (optional)
- Mobile-responsive interface
- Optional local username/password login with roles
- Separate independent Mandi Profiles / databases
- e-Anugya integration guide and official portal shortcut (no password/PIN/OTP/CAPTCHA storage or bypass)

## Windows setup

1. Keep your existing `mandi.db` if you already have data.
2. Install Python 3.10 or newer.
3. Open Command Prompt in this project folder.
4. Run:

```text
pip install -r requirements.txt
python app.py
```

5. Open `http://127.0.0.1:5000`.

You can also double-click `start_mandi_tracker.bat`.

## Existing data safety

Profile 1 continues to use `mandi.db`. New profiles use files such as `mandi_profile_2.db`. Never delete your database files while upgrading.

## Optional Excel folder sync

Open **Settings → Excel Sync**, enter a folder such as `C:\Mandi\Excel`, and enable Auto-sync. The app checks that folder for Excel files and uses duplicate protection.

## Optional login security

Open **Settings → Login Security** and enable login. Create the first account from **Manage Users**. If you enable it, keep your username/password safe.

## e-Anugya

The e-Anugya page intentionally does not request or store your portal password, PIN, OTP or CAPTCHA. Complete the official portal login yourself and import permitted downloaded Excel reports into the tracker.

## Phone access on the same Wi-Fi

Start the app on the PC, run `ipconfig`, find the PC IPv4 address, then on the phone open:

`http://YOUR-PC-IP:5000`

Both devices must be on the same Wi-Fi. Windows Firewall may need to allow Python on your private network.


## Mobile / PWA edition
This edition includes a responsive mobile interface, mobile bottom navigation, installable PWA manifest/service worker, an install prompt on supported browsers, camera/document upload on the Add Purchase form, and a small offline shell cache. The server/database remains the source of truth.

### Install on Android
1. Start the tracker on a PC or host it over HTTPS.
2. Open the site in Chrome on Android.
3. Use the browser's Install/Add to Home Screen option.
4. For camera upload, open Purchases → Add Purchase → Bill/Payment-Patrak/Document and use the camera/file picker.

### Important
A Flask app running on a PC can be opened from a phone on the same Wi-Fi, but Chrome service workers/PWA installation generally require HTTPS (localhost is also treated as secure). Cloud deployment requires a hosting account/domain or server credentials; this package is prepared for that step but does not silently access an external hosting account.
