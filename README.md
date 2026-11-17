# Sinfdoshlar xotiralar arxivi

Python 3.9 yoki undan yangi versiyasi kerak. Ilova Python standart kutubxonasidan foydalanadi.

## Lokal ishga tushirish (Windows)

1. `.env.example` faylini `.env` nomi bilan nusxalang va `ADMIN_USERNAME` hamda `ADMIN_PASSWORD` qiymatlarini o'zgartiring.
2. PowerShell'da loyiha papkasidan `py server.py` buyrug'ini bajaring.
3. `http://127.0.0.1:8000` manzilini oching.

Lokal HTTP uchun `COOKIE_SECURE=0`; HTTPS hostingda esa `COOKIE_SECURE=1` ishlatiladi. Dastlabki video ixtiyoriy: `INITIAL_VIDEO_URL` qiymati bo'sh qoldirilishi mumkin.

## Render'ning bepul tarifiga joylash

Loyiha ildizidagi `render.yaml` bepul Render Blueprint sozlamasidir. U Python 3.13, Frankfurt hududi, HTTPS cookie va Render bergan portdan foydalanadi. `DATA_DIR` vaqtinchalik diskka yo'naltirilgan; doimiy disk bepul tarifda mavjud emas.

1. Loyihani GitHub repository'ga yuboring. `.env` va `data/store.json` ni GitHub'ga yuklamang.
2. Render'da **New > Blueprint** ni tanlab, repository'ni ulang.
3. `ADMIN_USERNAME` va `ADMIN_PASSWORD` maxfiy qiymatlarini kiriting.
4. Blueprint'ni ishga tushiring va deploy loglaridagi manzilni ochib, saytni tekshiring.

Render Free 15 daqiqa so'rov kelmasa xizmatni uxlatadi; uyg'onishi bir daqiqagacha cho'zilishi mumkin. Xizmat uxlaganda, qayta ishga tushganda yoki qayta joylanganda vaqtinchalik `store.json` yo'qolishi mumkin. Natijada ro'yxatdan o'tgan foydalanuvchilar, tasdiqlar, videolar ro'yxati va panelda almashtirilgan admin paroli tiklanmaydi; R2 ichidagi video fayllar o'chmaydi, lekin ularning saytdagi ro'yxatini qayta qo'shish kerak bo'lishi mumkin. Render bu cheklovlarni bepul tarif shartlarida ko'rsatadi ([Render bepul tarif cheklovlari](https://render.com/docs/free)).

`render.yaml` ichida parol yoki token yo'q; maxfiy qiymatlarni faqat Render Dashboard'da kiriting. Sayt bitta nusxada ishlaydi. R2 videolar o'zining HTTPS manzili bilan ochiladi.

## Linux serverga joylash (systemd + Caddy)

`deploy/` ichida systemd service, Caddy va production environment namunalar bor. Ular Debian/Ubuntu turidagi serverlar uchun boshlang'ich andoza hisoblanadi.

1. Domeningizning DNS A/AAAA yozuvlarini serverga yo'naltiring, tashqaridan 80 va 443 portlarini oching. Caddy ommaviy domen orqali HTTPS sertifikatini avtomatik olishi va yangilashi uchun shu shartlar kerak ([Caddy Automatic HTTPS](https://caddyserver.com/docs/automatic-https)).
2. Ilova fayllarini `/opt/sinfdoshlar` ichiga joylang. `server.py`, `public/` va `deploy/` birga bo'lishi kerak.
3. `sinfdoshlar` tizim foydalanuvchisini yarating, `/var/lib/sinfdoshlar` ma'lumot katalogini unga yozishga ruxsat bering.
4. `deploy/sinfdoshlar.env.example` ni `/etc/sinfdoshlar.env` ga nusxalang. Admin login/parolini noyob kuchli qiymatga o'zgartiring. Faylga faqat root o'qiy oladigan ruxsat bering (`chmod 600`).
5. Environment fayldagi `DATA_DIR=/var/lib/sinfdoshlar` ni saqlang. `COOKIE_SECURE=1` bo'lsin. `.env` yoki environment qiymatlarini Git'ga qo'shmang.
6. `deploy/sinfdoshlar.service.example` ni `/etc/systemd/system/sinfdoshlar.service` ga o'rnating; Caddy namunadagi `example.com` ni o'z domeningizga almashtirib, Caddyfile'i o'rnating. So'ng `systemctl daemon-reload`, `systemctl enable --now sinfdoshlar`, `systemctl reload caddy` bajaring.
7. `systemctl status sinfdoshlar caddy` va `journalctl -u sinfdoshlar -n 100` bilan tekshiring.

Ilova bitta Python worker/process bilan ishlashi kerak: sessiyalar va bloklash sessiyalar jadvali jarayon xotirasida turadi. Bir necha nusxani parallel ishga tushirish sessiya holatini bo'lishmaydi. Caddy reverse proxy oldida turganda ilova faqat loopback (`127.0.0.1`) ga bog'lanadi va ishonchli proxy IP'sidan kelgan `X-Real-IP` qiymatiga tayanadi. Caddy namunasi shu sarlavhani beradi; Caddy so'rovdan olgan client IP uchun `{client_ip}` placeholder'ini ishlatadi ([Caddy reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy), [Caddy client IP](https://caddyserver.com/docs/caddyfile/options#trusted_proxies)).

Video, foydalanuvchi va admin parol hash ma'lumotlari `DATA_DIR/store.json` da saqlanadi. `/var/lib/sinfdoshlar` ni muntazam zaxiralang va servis foydalanuvchisiga yozish ruxsatini cheklang. Admin paneldagi parolni almashtirish store'da hash'ni yangilaydi; keyin yangi parol bilan kiring.

## Bloklash qanday ishlaydi

Admin foydalanuvchini bloklaganda blok holati diskka yoziladi va shu jarayondagi faol sessiyalari bekor qilinadi. Har bir himoyalangan API so'rovda akkaunt holati qayta tekshiriladi, qayta kirish rad etiladi; boshqa ilova nusxalari ham diskdagi blok holatini ko'radi. Ochiq brauzer oynasi sessiyani 15 soniyagacha bo'lgan navbatdagi tekshiruvda yopadi. Bu foydalanuvchi akkaunti orqali sayt API va sahifa funksiyalariga kirishni to'xtatadi. R2 video havolasi ommaga ochiq bo'lsa, uni bilgan odam sayt tashqarisidan ham ochishi mumkin; to'liq cheklash uchun R2 bucket'ni private qilib, qisqa muddatli signed URL yoki autentifikatsiyali video proxy ishlatish kerak.

## Video manbasi va cheklovlar

HTTPS MP4/WebM/OGG havolalari brauzerda ijro qilinadi. YouTube, Vimeo, MEGA kabi iframe manbalari o'z player boshqaruvlari va qoidalarini saqlaydi; ayrim manbalar iframe'ni taqiqlaydi. Brauzer ichida video ijrosini ekran yozuvi yoki tashqi yuklab olishdan mutlaq himoyalashning texnik kafolati yo'q.
