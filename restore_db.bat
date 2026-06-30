@echo off
title MongoDB Ma'lumotlarini Tiklash
cls
echo =========================================
echo    MONGODB BAZASINI TIKLASH (RESTORE)
echo =========================================
echo.

:: Python bormi yoki yo'qligini tekshirish
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Xatolik: Python o'rnatilmagan yoki PATH-ga qo'shilmagan!
    pause
    exit /b
)

if not exist database_backup.json (
    echo Xatolik: database_backup.json topilmadi!
    echo Avval download_db.py orqali backup oling.
    pause
    exit /b
)

:: Scriptni ishga tushirish
python restore_db.py

echo.
echo =========================================
echo    JARAYON YAKUNLANDI
echo =========================================
pause
