[app]
title = Checklist Qualidade
package.name = checklistqualidade
package.domain = br.com.suaempresa

source.dir = .
source.include_exts = py,kv,png,jpg,jpeg,json,txt,env,pdf
source.exclude_dirs = .git,.github,__pycache__,bin,.buildozer,venv,.venv

version = 0.1.0

requirements = python3,kivy,requests,tzdata,pillow,urllib3,chardet,idna,certifi

orientation = portrait
fullscreen = 0

android.permissions = INTERNET,CAMERA,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
android.api = 34
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True

p4a.branch = master

[buildozer]
log_level = 2
warn_on_root = 0
