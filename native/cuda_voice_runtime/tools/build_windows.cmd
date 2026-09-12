@echo off
setlocal

rem Build script for the self-written CUDA voice runtime.
rem The CUDA toolkit and Visual Studio are BUILD-TIME tools only; the resulting
rem DLL loads nvcuda.dll at run time and needs nothing but the NVIDIA driver.

set "VSROOT=%ProgramFiles%\Microsoft Visual Studio\2022\Community"
set "VCVARS=%VSROOT%\VC\Auxiliary\Build\vcvars64.bat"
set "CMAKE=%VSROOT%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
set "SHORT_TEMP=C:\fsv-cuda-check\tmp"
set "BUILD_DRIVE=F:"
rem One DLL has to cover every card the driver can JIT for. compute_61 is Pascal
rem (GTX 10xx) and newer; see CMakeLists.txt for the measurements behind it.
set "FSV_PTX_ARCH=compute_61"

if not exist "%VCVARS%" (
  echo Missing VS environment: %VCVARS%
  exit /b 2
)
if not exist "%CMAKE%" (
  echo Missing VS bundled CMake: %CMAKE%
  exit /b 3
)
if not exist "%SHORT_TEMP%" mkdir "%SHORT_TEMP%"

call "%VCVARS%"
if errorlevel 1 exit /b %errorlevel%
set "TEMP=%SHORT_TEMP%"
set "TMP=%SHORT_TEMP%"

rem CUDA 12.6 mishandles non-ASCII build paths, so compile through an ASCII drive.
subst %BUILD_DRIVE% "%~dp0..\..\.." >nul 2>&1
if errorlevel 1 (
  echo Failed to map the ASCII build drive %BUILD_DRIVE%
  exit /b 4
)
cd /d %BUILD_DRIVE%\
if errorlevel 1 exit /b %errorlevel%

"%CMAKE%" -S %BUILD_DRIVE%\native\cuda_voice_runtime -B %BUILD_DRIVE%\build\cuda_voice_runtime -G "Visual Studio 17 2022" -A x64 -DFSV_PTX_ARCH=%FSV_PTX_ARCH%
if errorlevel 1 (
  subst %BUILD_DRIVE% /d >nul 2>&1
  exit /b %errorlevel%
)
"%CMAKE%" --build %BUILD_DRIVE%\build\cuda_voice_runtime --config Release --parallel 2
set "BUILD_EXIT=%errorlevel%"
subst %BUILD_DRIVE% /d >nul 2>&1
exit /b %BUILD_EXIT%
