"""
care/management/commands/check_services.py

services.py가 제대로 도는지 눈으로 확인하는 명령입니다.
API를 만들기 전에 계산이 맞는지 먼저 봐야 나중에 디버깅이 쉽습니다.

    poetry run python manage.py check_services
    poetry run python manage.py check_services --day 30
"""
from django.core.management.base import BaseCommand

from care.models import Surgery
from care.services import (care_items, completion, next_unlock, stage_of,
                           timeline, validate_task_key)


class Command(BaseCommand):
    help = "services.py 동작 확인"

    def add_arguments(self, parser):
        parser.add_argument("--day", type=int, default=None, help="확인할 D+N (기본: 2, 30 둘 다)")
        parser.add_argument("--lang", default="ko")

    def handle(self, *args, **opt):
        s = Surgery.objects.first()
        if s is None:
            self.stdout.write(self.style.ERROR("Surgery가 없습니다. seed_demo를 먼저 실행하세요."))
            return

        lang = opt["lang"]
        days = [opt["day"]] if opt["day"] is not None else [2, 30]

        self.stdout.write(self.style.SUCCESS(
            f"\n환자 {s.patient.patient_code} · 수술일 {s.surgery_date} · 귀국 D+{s.return_day}\n"
        ))

        for day in days:
            ci = care_items(s, day, lang)
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"─── D+{day} ({s.date_of(day)}) · {stage_of(s, day, lang)} ───"
            ))

            self.stdout.write(f"오늘 할 일 {len(ci['tasks'])}건")
            for t in ci["tasks"]:
                mark = "" if t["source"] == "template" else f"  ← {t['source']}"
                self.stdout.write(
                    f"   {t['icon']} {t['name']:<18} {t['done_count']}/{t['times_per_day']}회{mark}"
                )

            r = ci["rules"]
            self.stdout.write(
                f"오늘 해도 될까 — 가능 {r['ok']['count']} / 주의 {r['care']['count']} / 금지 {r['no']['count']}"
            )
            for status in ("ok", "care", "no"):
                for it in r[status]["items"][:3]:
                    self.stdout.write(f"   [{status:4}] {it['icon']} {it['name']} — {it['text']}")
                if r[status]["count"] > 3:
                    self.stdout.write(f"   ... 외 {r[status]['count'] - 3}건")

            self.stdout.write(f"완주율 {completion(s, day) * 100:.1f}%")
            if nu := next_unlock(s, day, lang):
                self.stdout.write(f"다음 해금 D+{nu['day']} — {nu['label']}")
            self.stdout.write("")

        self.stdout.write(self.style.MIGRATE_HEADING("─── 앞으로의 변화 ───"))
        for ev in timeline(s, lang):
            self.stdout.write(f"   D+{ev['day']:<4} [{ev['badge']}] {ev['label']}")

        self.stdout.write(self.style.MIGRATE_HEADING("\n─── task_key 검증 ───"))
        for key in ("ice", "medication", "oops"):
            ok = validate_task_key(s, 2, key)
            self.stdout.write(f"   {key:<12} {'통과' if ok else '거부 (400)'}")
