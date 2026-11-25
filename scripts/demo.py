"""Build a clearly labelled, isolated local demonstration using synthetic input."""

import os
import tempfile
import json
from pathlib import Path
from datetime import timedelta
from project import iso, START, MEDIA, FORUM, KEYWORDS, HK, write_rows, ROOT


def main():
    with tempfile.TemporaryDirectory() as directory:
        # Reload modules in a child so its private-data root is isolated.
        import subprocess, sys

        path = Path(directory)
        cutoff = START + timedelta(days=24, hours=10)
        for platform in MEDIA:
            out = path / MEDIA[platform]
            items = []
            for i in range(24):
                for j, c in enumerate(KEYWORDS):
                    title = (
                        c
                        + [
                            " earns praise for a helpful new tool",
                            " faces criticism after a disappointing test",
                            " releases a model update",
                        ][(i + j) % 3]
                    )
                    time = START + timedelta(days=i, hours=j)
                    items.append(
                        dict(
                            id=f"{platform}_{i}_{j}",
                            platform=platform,
                            content_type="article",
                            title=title,
                            body="",
                            url=(
                                f"https://www.bbc.com/news/articles/example{i}{j}"
                                if platform == "bbc"
                                else ""
                            ),
                            published_at=iso(time) if platform != "bbc" else "",
                            published_date=time.astimezone(HK).date().isoformat(),
                            language="en",
                            **{k: int(k == c) for k in KEYWORDS},
                        )
                    )
            write_rows(out / f"{platform}_articles.csv", items, list(items[0]))
            (out / f"{platform}_state.json").write_text(
                json.dumps(
                    {c + "|" + w: iso(cutoff) for c, ts in KEYWORDS.items() for w in ts}
                )
            )
        items = []
        for p in FORUM:
            for day in range(24):
                for j, c in enumerate(KEYWORDS):
                    items.append(
                        dict(
                            id=f"{p}-{day}-{j}",
                            platform=p,
                            content_type="comment",
                            title="",
                            body=f"{c} "
                            + [
                                "works wonderfully for me.",
                                "is awful in my tests.",
                                "released a model today.",
                            ][(day + j) % 3],
                            published_at=iso(START + timedelta(days=day, hours=j)),
                            text_scope="full",
                            url="",
                            companies=[
                                "openai",
                                "anthropic",
                                "google_deepmind",
                                "deepseek",
                                "bytedance_seed",
                            ][j],
                        )
                    )
        out = path / "Collected Data"
        write_rows(out / "forum_raw.csv", items, list(items[0]))
        runs = [
            dict(
                platform=p,
                window_start=iso(START),
                window_end=iso(cutoff),
                status="ok",
                run_at=iso(cutoff),
            )
            for p in FORUM
        ]
        write_rows(out / "forum_collection_runs.csv", runs, list(runs[0]))
        (path / "latest.json").write_text(
            json.dumps(
                dict(
                    cutoff=iso(cutoff),
                    finished=iso(cutoff),
                    sources={p: "ok" for p in (*MEDIA, *FORUM)},
                )
            )
        )
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/build.py")],
            env=dict(
                os.environ, OBS_STATE=directory, OBS_OUTPUT=str(ROOT / "demo-dist")
            ),
            check=True,
        )
        index = ROOT / "demo-dist/index.html"
        index.write_text(
            index.read_text().replace(
                "<main>",
                '<main><aside class="demoflag">Demo · All dates, examples and scores are illustrative.</aside>',
            )
        )
        print("Preview: python -m http.server 8000 --directory demo-dist")


if __name__ == "__main__":
    main()
