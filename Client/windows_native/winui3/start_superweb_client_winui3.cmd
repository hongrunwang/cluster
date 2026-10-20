@echo off
setlocal
cd /d "%~dp0\..\.."

set CONFIGURATION=Release
set BUILD_ONLY=0
set "ORIGINAL_ARGS=%*"

:parse_args
if "%~1"=="" goto after_args
if /I "%~1"=="--debug" (
  set CONFIGURATION=Debug
  shift
  goto parse_args
)
if /I "%~1"=="--release" (
  set CONFIGURATION=Release
  shift
  goto parse_args
)
if /I "%~1"=="--build-only" (
  set BUILD_ONLY=1
  shift
  goto parse_args
)
shift
goto parse_args

:after_args
if "%BUILD_ONLY%"=="0" (
  net session >nul 2>&1
  if errorlevel 1 (
    echo Requesting administrator privileges for Windows Firewall mDNS rules...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -WorkingDirectory '%CD%' -ArgumentList '%ORIGINAL_ARGS%' -Verb RunAs"
    exit /b
  )
)

where dotnet >nul 2>&1
if errorlevel 1 (
  echo dotnet SDK was not found in PATH.
  echo Install the .NET SDK required by Client\global.json and try again.
  pause
  exit /b 1
)

set NATIVE_DLL=windows_native\backend\build\Release\superweb_client_backend.dll
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
  echo.
  echo Native backend configure step failed.
  pause
  exit /b 1
)

"%CMAKE_EXE%" --build windows_native\backend\build --config Release
if errorlevel 1 (
  echo.
  echo Native backend build failed.
  pause
  exit /b 1
)

if not exist "%NATIVE_DLL%" (
  echo.
  echo Native backend DLL was not found at:
  echo   %NATIVE_DLL%
  pause
  exit /b 1
)

echo Building SuperWebClient.WinUI3 ^(%CONFIGURATION%^)^...
dotnet msbuild windows_native\winui3\SuperWebClient.WinUI3.csproj -t:Build -restore -p:Configuration=%CONFIGURATION% -p:Platform=x64 -m:1 -nr:false
if errorlevel 1 (
  echo.
  echo WinUI3 build failed.
  pause
  exit /b 1
)

set APP_EXE=windows_native\winui3\bin\x64\%CONFIGURATION%\net8.0-windows10.0.19041.0\win-x64\SuperWebClient.WinUI3.exe
if not exist "%APP_EXE%" (
  echo.
  echo Built output was not found at:
  echo   %APP_EXE%
  pause
  exit /b 1
)

if "%BUILD_ONLY%"=="1" (
  echo.
  echo Build completed successfully:
  echo   %APP_EXE%
  exit /b 0
)

echo Launching SuperWebClient.WinUI3...
"%APP_EXE%"
set EXIT_CODE=%ERRORLEVEL%
if not "%EXIT_CODE%"=="0" (
  echo.
  echo SuperWebClient.WinUI3 exited with code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
