@echo off
cls
echo =========================================
echo    GITHUBGA O'ZGARISHLARNI YUKLASH
echo =========================================
echo.

:: Git bormi yoki yo'qligini tekshirish
git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Xatolik: Git o'rnatilmagan!
    pause
    exit /b
)

:: O'zgarishlarni qo'shish
echo 1. O'zgarishlar qo'shilmoqda...
git add .

:: Commit xabarini so'rash
echo.
set /p msg="Commit xabarini kiriting (bo'sh qoldirsangiz 'update' bo'ladi): "
if "%msg%"=="" set msg=update

:: Commit qilish
echo.
echo 2. O'zgarishlar saqlanmoqda...
git commit -m "%msg%"

:: Push qilish (GitHub-ga yuklash)
echo.
echo 3. GitHub-ga yuborilmoqda...
git push

echo.
echo =========================================
echo    AMAL MUVAFFAQIYATLI YAKUNLANDI!
echo =========================================
pause
