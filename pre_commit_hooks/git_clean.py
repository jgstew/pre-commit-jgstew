"""git clean pre-commit hook."""

import subprocess


def git_clean():
    """do git clean -f"""
    run_cmd = ["git", "clean", "-f"]
    run_output = ""
    print(f"Running: {run_cmd}")
    try:
        run_output = subprocess.check_output(run_cmd).decode()
    except subprocess.CalledProcessError as err:
        print(err)

    print(run_output)
    return run_output.count("Removing ")


def main(argv=None):
    """execution starts here"""
    return git_clean()


if __name__ == "__main__":
    exit(main())
