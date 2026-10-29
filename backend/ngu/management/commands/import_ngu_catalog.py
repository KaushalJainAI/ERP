"""Import the NGU online-store catalogue (products/variants/combos) into the crosswalk.

Catalogue only -- no orders, invoices, payments, refunds or stock. Idempotent;
never creates or changes ERP Items. Source: ``--dsn`` (Postgres URL),
``--docker CONTAINER`` (psql inside a container) or ``--json FILE``.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from ngu.sync import fetch_from_docker, fetch_from_dsn, sync_catalog


class Command(BaseCommand):
    help = "Import the NGU catalogue into the NguCatalogEntry crosswalk (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--dsn", help="Postgres URL of the NGU database")
        parser.add_argument(
            "--docker", help="Docker container running the NGU Postgres"
        )
        parser.add_argument(
            "--db", default="ngu_inspect", help="Database name (with --docker)"
        )
        parser.add_argument(
            "--json", help="Catalogue JSON file (as produced by the fetch SQL)"
        )

    def handle(self, *args, **opts):
        if opts["json"]:
            with open(opts["json"], encoding="utf-8") as fh:
                data = json.load(fh)
        elif opts["dsn"]:
            data = fetch_from_dsn(opts["dsn"])
        elif opts["docker"]:
            data = fetch_from_docker(opts["docker"], opts["db"])
        else:
            raise CommandError("Give one of --dsn, --docker or --json.")
        counts = sync_catalog(data)
        self.stdout.write(
            self.style.SUCCESS(
                "NGU catalogue: {variants} variants, {combos} combos "
                "({created} created, {updated} updated), {linked} auto-linked by exact name.".format(
                    **counts
                )
            )
        )
