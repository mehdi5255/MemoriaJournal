# Lance le worker Celery (Redis doit tourner : docker start redis)
Set-Location "$PSScriptRoot\MemoriA"
& "$PSScriptRoot\env\Scripts\celery.exe" -A MemoriA worker -l info --pool=solo
