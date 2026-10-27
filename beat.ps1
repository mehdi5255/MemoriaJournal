# Lance les tâches planifiées Celery Beat (Redis doit tourner : docker start redis)
Set-Location "$PSScriptRoot\MemoriA"
& "$PSScriptRoot\env\Scripts\celery.exe" -A MemoriA beat -l info
