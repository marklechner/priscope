import argparse
import json
import os
import re
from datetime import datetime, timedelta

import requests
from dateutil.relativedelta import relativedelta
from termcolor import colored

from logo import get_logo


def load_config():
    with open("config.json", "r") as config_file:
        return json.load(config_file)


CONFIG = load_config()


def get_pr_diff(diff_url):
    response = requests.get(diff_url)
    if response.status_code == 200:
        return response.text
    else:
        print(f"Error fetching diff: {response.status_code}")
        return None


def parse_time_string(time_string):
    match = re.match(r"(\d+)([dwm])", time_string)
    if not match:
        raise ValueError(
            "Invalid time string format. Use format like '2d', '3w', or '1m'."
        )

    value, unit = match.groups()
    value = int(value)

    if unit == "d":
        return timedelta(days=value)
    elif unit == "w":
        return timedelta(weeks=value)
    elif unit == "m":
        return relativedelta(months=value)


def get_merged_prs(repo_url, limit=None, since=None):
    # Extract owner and repo name from the URL
    parts = repo_url.split("/")
    owner = parts[-2]
    repo = parts[-1]

    # GitHub Search API endpoint for merged pull requests
    api_url = f"https://api.github.com/search/issues"

    # Parameters for the API request
    params = {
        "q": f"repo:{owner}/{repo} is:pr is:merged",
        "sort": "updated",
        "order": "desc",
        "per_page": CONFIG["max_prs_per_page"],
    }

    if since:
        params["q"] += f" merged:>={since.isoformat()}"

    merged_prs = []
    page = 1

    while True:
        params["page"] = page
        response = requests.get(api_url, params=params, headers={"Accept": "application/vnd.github.v3+json"})

        if response.status_code != 200:
            print(f"Error: Unable to fetch pull requests. Status code: {response.status_code}")
            return []

        search_results = response.json()
        pull_requests = search_results.get("items", [])

        if not pull_requests:
            break

        for pr in pull_requests:
            # Fetch additional PR details
            pr_details = requests.get(pr["pull_request"]["url"]).json()
            pr_info = {
                "number": pr["number"],
                "title": pr["title"],
                "description": pr["body"],
                "merged_at": pr_details["merged_at"],
                "diff": get_pr_diff(pr_details["diff_url"]),
            }
            merged_prs.append(pr_info)

            if limit and len(merged_prs) >= limit:
                return merged_prs

        # Check if there's a next page
        if page * CONFIG["max_prs_per_page"] >= search_results["total_count"]:
            break

        page += 1

    return merged_prs


def analyze_pr_with_ollama(pr_info):
    #print(pr_info)
    ollama_url = f"{CONFIG['ollama_url']}{CONFIG['api_endpoint']}"
    prompt = f"""
    Carefully analyze the following pull request for actual security risks or malicious code changes. Focus only on significant security issues, such as the introduction of vulnerabilities, hardcoded secrets, backdoors, insecure configurations, or risky dependencies.
    
    If the changes do not represent a clear and actionable security risk, respond only with: "No issues identified."
    
    Only provide a brief summary if a security risk or malicious change is clearly identified and explain why it is a risk, based on concrete indicators such as unsafe functions, insecure patterns, or sensitive data exposure.

    PR Number: {pr_info['number']}
    Title: {pr_info['title']}
    Description: {pr_info['description']}

    Code Changes:
    {pr_info['diff']}
    """

    data = {"model": CONFIG["model_name"], "prompt": prompt, "stream": False}

    response = requests.post(ollama_url, json=data)
    if response.status_code == 200:
        analysis = response.json()["response"].strip()
        return analysis
    else:
        return f"Error analyzing PR: {response.status_code}"

    #return "testing"


def generate_json_report(repo_url, analyzed_prs, suspicious_count):
    report = {
        "repository": repo_url,
        "scan_date": datetime.now().isoformat(),
        "total_prs_analyzed": len(analyzed_prs),
        "suspicious_prs_count": suspicious_count,
        "analyzed_prs": analyzed_prs,
    }
    return json.dumps(report, indent=2)


def main() -> None:
    print(colored(get_logo(), "cyan"))

    parser = argparse.ArgumentParser(
        description="Analyze merged PRs from a GitHub repository for security risks."
    )
    parser.add_argument("repo_url", help="GitHub repository URL")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-n",
        "--number",
        type=int,
        default=10,
        help="Number of PRs to analyze (default: 10)",
    )
    group.add_argument(
        "-s",
        "--since",
        help="Analyze PRs merged since this time (format: 2d, 3w, 1m for days, weeks, months)",
    )
    parser.add_argument(
        "-j", "--json", action="store_true", help="Generate JSON report"
    )
    args = parser.parse_args()

    if args.since:
        try:
            lookback = parse_time_string(args.since)
            since_date = datetime.now() - lookback
            print(
                colored(
                    f"\nAnalyzing PRs merged since {since_date.strftime('%Y-%m-%d %H:%M:%S')} for {args.repo_url}",
                    "yellow",
                )
            )
            merged_prs = get_merged_prs(args.repo_url, since=since_date)
        except ValueError as e:
            print(colored(f"Error: {str(e)}", "red"))
            return
    else:
        print(
            colored(
                f"\nAnalyzing the last {args.number} PRs for {args.repo_url}", "yellow"
            )
        )
        merged_prs = get_merged_prs(args.repo_url, limit=args.number)

    print(colored("=" * 50, "yellow"))

    suspicious_prs = 0
    analyzed_prs = []

    for pr in merged_prs:
        print(f"\nAnalyzing PR #{pr['number']}")
        analysis = analyze_pr_with_ollama(pr)

        pr_result = {
            "number": pr["number"],
            "title": pr["title"],
            "merge_date": pr["merged_at"],
            "link": f"https://github.com/{args.repo_url.split('/')[-2]}/{args.repo_url.split('/')[-1]}/pull/{pr['number']}",
            "analysis": analysis,
        }

        if "No issues identified" in analysis:
            print(colored(f"PR #{pr['number']} -> No issues", "green"))
            pr_result["status"] = "clean"
        else:
            suspicious_prs += 1
            print(colored(f"PR #{pr['number']} -> Potential Issue", "red"))
            print(colored("PR Title: ", "yellow") + pr["title"])
            print(colored("PR Link: ", "yellow") + pr_result["link"])
            print(colored("PR Merge Date: ", "yellow") + pr["merged_at"])
            print(colored("Finding: ", "yellow") + analysis)
            pr_result["status"] = "suspicious"

        analyzed_prs.append(pr_result)

    print(colored("\nAnalysis Summary", "blue"))
    print(colored("=" * 20, "blue"))
    print(colored(f"Number of PRs analyzed: {len(merged_prs)}", "blue"))
    color = "red" if suspicious_prs > 0 else "blue"
    print(colored(f"Number of suspicious PRs: {suspicious_prs}", color))

    if args.json:
        json_report = generate_json_report(args.repo_url, analyzed_prs, suspicious_prs)
        report_filename = f"priscope_report_{args.repo_url.split('/')[-1]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        # Use current directory if not running in Docker, otherwise use /app/output
        output_dir = "/app/output" if os.path.exists("/app/output") else "."
        output_path = os.path.join(output_dir, report_filename)

        with open(output_path, "w") as f:
            f.write(json_report)
        print(colored(f"\nJSON report generated: {output_path}", "green"))

    print(colored("\nEnd of process. Exiting.", "green"))


if __name__ == "__main__":
    main()
