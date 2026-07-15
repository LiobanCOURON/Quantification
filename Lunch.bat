@echo off
setlocal enableextensions
chcp 65001 >nul 2>&1
title Quantification

REM ---------------------------------------------------------------
REM Launcher wrapper: forward to the real script inside
REM Quantification_replacement/, resolving the path relative to THIS
REM file so it works from any current working directory.
REM ---------------------------------------------------------------

call "%~dp0Quantification_replacement\lunch.bat"