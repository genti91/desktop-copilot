"""Deriva FIRMWARE_BUILD de la cantidad de commits del repo.

Asi el numero sube solo en cada push y no hay que acordarse de editarlo a mano.
Como local y CI calculan lo mismo para un commit dado, un build local nunca
queda "por debajo" del publicado y el ESP32 no se autoactualiza encima de una
prueba local; recien lo hace cuando CI compila un commit posterior.
"""

Import("env")  # noqa: F821  (lo inyecta PlatformIO)

import subprocess


def git_commit_count() -> int:
    try:
        output = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return int(output.stdout.strip())
    except Exception as error:
        print(f"[build_number] Sin git ({error}); uso build 0.")
        return 0


build_number = git_commit_count()
print(f"[build_number] FIRMWARE_BUILD={build_number}")
env.Append(CPPDEFINES=[("FIRMWARE_BUILD", build_number)])  # noqa: F821
