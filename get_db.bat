@echo off
title MongoDB Ma'lumotlarini Yuklash
cls
echo =========================================
echo    MONGODB BAZASINI YUKLAB OLISH
echo =========================================
echo.

:: Python bormi yoki yo'qligini tekshirish
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Xatolik: Python o'rnatilmagan yoki PATH-ga qo'shilmagan!
    pause
    exit /b
)

:: Scriptni ishga tushirish
echo Ma'lumotlar yuklanmoqda, iltimos kuting...
echo.
python download_db.py

echo.
echo =========================================
echo    JARAYON YAKUNLANDI
echo =========================================
pause
