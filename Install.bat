@echo off
setlocal enableextensions
chcp 65001 >nul 2>&1
title Installation - Quantification

REM ---------------------------------------------------------------
REM Installer wrapper: forward to the real script inside
REM Quantification_replacement/, resolving the path relative to THIS
REM file so it works from any current working directory.
REM ---------------------------------------------------------------

call "%~dp0Quantification_replacement\install.bat"