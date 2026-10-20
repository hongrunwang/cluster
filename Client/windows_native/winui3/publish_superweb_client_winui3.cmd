@echo off
setlocal
cd /d "%~dp0\..\.."

set CONFIGURATION=Release
set DIST_ROOT=windows_native\winui3\dist
set DIST_APP=%DIST_ROOT%\app
set DIST_ZIP=%DIST_ROOT%\superweb-client-winui3-portable.zip
set TEMP_ZIP=%TEMP%\superweb-client-winui3-portable.zip

if /I "%~1"=="--debug" (
  set CONFIGURATION=Debug
)

set STAGE_ROOT=%TEMP%\superweb-client-winui3-%CONFIGURATION%-stage
set STAGE_APP=%STAGE_ROOT%\app
set STAGE_START=%STAGE_ROOT%\start_superweb_client_winui3.cmd
set STAGE_README=%STAGE_ROOT%\README.txt

where dotnet >nul 2>&1
if errorlevel 1 (
  echo dotnet SDK was not found in PATH.
  echo Install the .NET SDK required by Client\global.json and try again.
  pause
  exit /b 1
)

set NATIVE_DLL=windows_native\backend\build\Release\superweb_client_backend.dll
if not exist "%NATIVE_DLL%" (
  set "CMAKE_EXE=cmake"
  where cmake >nul 2>&1
  if errorlevel 1 (
    if exist "%ProgramFiles(x86)%\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe" (
      set "CMAKE_EXE=%ProgramFiles(x86)%\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
    ) else (
      echo CMake was not found in PATH, and the Visual Studio CMake fallback path is unavailable.
      echo Build windows_native\backend\ manually or install CMake, then try again.
      pause
      exit /b 1
    )
  )

  echo Building native Windows backend DLL...
  "%CMAKE_EXE%" -S windows_native\backend -B windows_native\backend\build
  if errorlevel 1 (
    echo Native backend configure step failed.
    pause
    exit /b 1
  )

  "%CMAKE_EXE%" --build windows_native\backend\build --config Release
  if errorlevel 1 (
    echo Native backend build failed.
    pause
    exit /b 1
  )
)

if exist "%STAGE_ROOT%" rmdir /s /q "%STAGE_ROOT%"
mkdir "%STAGE_ROOT%"
if errorlevel 1 (
  echo Failed to create the temporary staging directory:
  echo   %STAGE_ROOT%
  pause
  exit /b 1
)

echo Publishing WinUI3 portable folder ^(%CONFIGURATION%^)^...
dotnet publish windows_native\winui3\SuperWebClient.WinUI3.csproj -c %CONFIGURATION% -r win-x64 -p:Platform=x64 -p:SelfContained=false -p:PublishSingleFile=false -o "%STAGE_APP%"
if errorlevel 1 (
  echo.
  echo WinUI3 publish failed.
  pause
  exit /b 1
)

(
  echo @echo off
  echo setlocal
  echo cd /d "%%~dp0"
  echo if not exist "%%~dp0app\SuperWebClient.WinUI3.exe" ^(
  echo   echo Could not find a published SuperWebClient.WinUI3.exe under app\
  echo   pause
  echo   exit /b 1
  echo ^)
  echo net session ^>nul 2^>^&1
  echo if errorlevel 1 ^(
  echo   echo Requesting administrator privileges for Windows Firewall mDNS rules...
  echo   powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%%~f0' -WorkingDirectory '%%CD%%' -Verb RunAs"
  echo   exit /b
  echo ^)
  echo set "MICROSOFT_WINDOWSAPPRUNTIME_BASE_DIRECTORY=%%~dp0app"
  echo "%%~dp0app\SuperWebClient.WinUI3.exe"
) > "%STAGE_START%"

(
  echo superweb-client WinUI3 portable distribution
  echo.
  echo 1. Double-click start_superweb_client_winui3.cmd
  echo 2. Allow administrator elevation if you want the app to add temporary UDP 5353 firewall rules for mDNS
  echo 3. If elevation is denied, the UI still opens and you can connect manually by host
  echo.
  echo Notes:
  echo - This build is framework-dependent to keep the distribution size reasonable.
  echo - The target Windows machine needs the .NET Desktop Runtime and Windows App Runtime that match this build.
) > "%STAGE_README%"

if not exist "%DIST_ROOT%" mkdir "%DIST_ROOT%"
if errorlevel 1 (
  echo Failed to create the distribution directory:
  echo   %CD%\%DIST_ROOT%
  pause
  exit /b 1
)

if exist "%TEMP_ZIP%" del /f /q "%TEMP_ZIP%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%STAGE_APP%','%STAGE_START%','%STAGE_README%' -DestinationPath '%TEMP_ZIP%' -CompressionLevel Optimal -Force"
if errorlevel 1 (
  echo.
  echo Packaging the portable ZIP failed.
  pause
  exit /b 1
)
if exist "%DIST_ZIP%" del /f /q "%DIST_ZIP%"
move /y "%TEMP_ZIP%" "%DIST_ZIP%" >nul

if not exist "%DIST_APP%" mkdir "%DIST_APP%"
robocopy "%STAGE_APP%" "%DIST_APP%" /MIR /R:2 /W:1 >nul
if errorlevel 8 (
  echo.
  echo The portable ZIP was created, but syncing the app folder into dist\app failed.
  echo Close any running copies of superweb-client and try again.
  pause
  exit /b 1
)

copy /y "%STAGE_START%" "%DIST_ROOT%\start_superweb_client_winui3.cmd" >nul
if errorlevel 1 (
  echo Failed to copy the portable launcher into:
  echo   %CD%\%DIST_ROOT%
  pause
  exit /b 1
)

copy /y "%STAGE_README%" "%DIST_ROOT%\README.txt" >nul
if errorlevel 1 (
  echo Failed to copy the portable README into:
  echo   %CD%\%DIST_ROOT%
  pause
  exit /b 1
)

if exist "%DIST_ROOT%\app.next" rmdir /s /q "%DIST_ROOT%\app.next" >nul 2>&1

echo.
echo Portable WinUI3 distribution completed:
echo   %CD%\%DIST_ROOT%
echo Shareable ZIP:
echo   %CD%\%DIST_ZIP%
exit /b 0
