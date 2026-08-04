@ECHO OFF

pushd %~dp0

if "%SPHINXBUILD%" == "" (
  set SPHINXBUILD=sphinx-build
)
if "%SOURCEDIR%" == "" (
  set SOURCEDIR=.
)
if "%BUILDDIR%" == "" (
  set BUILDDIR=_build
)

%SPHINXBUILD% -M %1 %SOURCEDIR% %BUILDDIR% -W --keep-going
if errorlevel 1 exit /b 1

popd
