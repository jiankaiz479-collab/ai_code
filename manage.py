#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cv_testing_site.settings')

    # ★★★ 新增這一段開始 ★★★
    # 如果指令是 runserver 且沒有指定 Port，就自動加上 8080
    if len(sys.argv) == 2 and sys.argv[1] == 'runserver':
        sys.argv.append('8080')
    # ★★★ 新增這一段結束 ★★★

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()