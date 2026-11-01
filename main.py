#!/usr/bin/env python3

import argparse
import logging
import os
import re
import sys

# PyJWT is not imported here on purpose: the scan is pure text
# matching and never calls the library, so importing it only added
# a startup failure on machines without the package.

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Files that could not be read or decoded during this run. A failed
# file is not a clean file, so main reports each one as not scanned
# and exits 1 when this list is not empty.
scan_failures = []

def setup_argparse():
    """
    Sets up the argument parser for the JWT Misconfiguration Checker.

    Returns:
        argparse.ArgumentParser: The configured argument parser.
    """
    parser = argparse.ArgumentParser(
        description="Scans code for common JWT misconfigurations.",
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument(
        "target",
        help="Path to the file or directory to scan."
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output (debug logging)."
    )

    parser.add_argument(
        "--check-secrets",
        action="store_true",
        help="Enable checks for hardcoded secrets."
    )

    parser.add_argument(
        "--output",
        help="Path to save results to a file."
    )

    return parser


def check_file(filepath, check_secrets):
    """
    Scans a single file for JWT misconfigurations.

    Args:
        filepath (str): The path to the file to scan.
        check_secrets (bool): Enable checks for hardcoded secrets.

    Returns:
        list: A list of detected misconfigurations.
    """
    misconfigurations = []

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

            # Check for 'alg: none'. The key may itself be quoted, as in a
            # JSON header or a Python dict ({"alg": "none"}), so an optional
            # quote is allowed between the name and the colon; requiring the
            # colon straight after "alg" missed the common dict form.
            if re.search(r"alg['\"]? *: *['\"]none['\"]", content, re.IGNORECASE):
                misconfigurations.append(f"Insecure 'alg: none' found in: {filepath}")

            # Check for HS256 with short/predictable secrets. The separator
            # may be a colon (dict or JSON) or an equals sign (a plain
            # assignment such as secret = "abc"); accepting only the colon
            # form missed the assignment form entirely.
            if check_secrets:
                secret_matches = re.findall(r"(\w*(?:secret|key)\w*)\s*[:=]\s*['\"]([^'\"]*)['\"]", content, re.IGNORECASE)
                for match in secret_matches:
                    secret_name, secret_value = match

                    # Heuristic for weak secrets (very short, predictable patterns)
                    # Every predictable entry is shorter than 16 characters,
                    # so the old if/elif made the list test unreachable,
                    # and a secret built by repeating a listed word
                    # ("passwordpassword", 16 characters) passed both.
                    predictable = ['secret', 'password', '123456', 'admin', 'test']
                    lowered = secret_value.lower()
                    repeated = any(lowered == word * (len(lowered) // len(word)) for word in predictable if len(lowered) % len(word) == 0)
                    if lowered in predictable or repeated or len(secret_value) < 16:
                        # The found value never goes into the report,
                        # in clear, on the console or in an output
                        # file. Only its length and whether it matched
                        # the predictable list are reported.
                        matched = lowered in predictable or repeated
                        misconfigurations.append(f"Potentially weak secret '{secret_name}' (length {len(secret_value)}, matched predictable list: {matched}) found in: {filepath}")


            # Check for missing expiration checks (e.g., not verifying exp
            # claim). This only means anything in a file that touches JWTs
            # at all; without the gate, every unrelated source file in a
            # scanned tree was reported as missing expiration handling.
            # I judge expiration handling per file, by text. A project that
            # checks exp in a shared helper still gets this finding for
            # every other file that mentions jwt and never says exp.
            if "jwt" in content.lower() and not re.search(r"exp|expiration", content, re.IGNORECASE):
                misconfigurations.append(f"Missing expiration claim handling detected in: {filepath}. Review JWT validation logic.")

            # Look for use of insecure or deprecated libraries and functions
            # Example: Suggesting the use of `jwt.decode` with proper algorithms verification.

            # Either quote style counts: algorithm="HS256" is just as common
            # in real code as the single quoted form.
            if re.search(r"jwt\.encode\(.*\balgorithm=['\"]HS256['\"]", content, re.IGNORECASE):
                misconfigurations.append(f"HS256 encoding found. Recommend considering stronger algorithms in: {filepath}")

            # Current PyJWT disables verification through an options
            # dict rather than verify=False, so both forms are checked.
            if re.search(r"jwt\.decode\(.*\bverify\s*=\s*False", content, re.IGNORECASE) or re.search(r"verify_signature['\"]?\s*:\s*False", content):
                 misconfigurations.append(f"Insecure verification disabled. Verify is set to false, this is unsafe. File: {filepath}")

    except FileNotFoundError:
        logging.error(f"File not found: {filepath}")
        scan_failures.append(filepath)
        return []  # Return empty list if file not found
    except Exception as e:
        logging.error(f"Error processing file {filepath}: {e}")
        scan_failures.append(filepath)
        return []

    return misconfigurations

def scan_directory(directory, check_secrets):
    """
    Scans a directory recursively for JWT misconfigurations.

    Args:
        directory (str): The path to the directory to scan.
        check_secrets (bool): Enable checks for hardcoded secrets.

    Returns:
        list: A list of detected misconfigurations.
    """
    misconfigurations = []
    for root, _, files in os.walk(directory):
        for file in files:
            # This filter applies only to directory scans. A single file
            # handed to main directly is scanned whatever its extension,
            # including a plain text note that happens to quote a token.
            if file.endswith((".py", ".js", ".java", ".go")):  # Example file extensions
                filepath = os.path.join(root, file)
                misconfigurations.extend(check_file(filepath, check_secrets))
    return misconfigurations

def main():
    """
    Main function to drive the JWT Misconfiguration Checker.
    """
    parser = setup_argparse()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.debug("Verbose logging enabled.")

    target = args.target
    check_secrets = args.check_secrets

    if os.path.isfile(target):
        misconfigurations = check_file(target, check_secrets)
    elif os.path.isdir(target):
        misconfigurations = scan_directory(target, check_secrets)
    else:
        print(f"Error: Target '{target}' is not a valid file or directory.")
        sys.exit(1)

    failed_files = list(scan_failures)

    if misconfigurations:
        print("JWT Misconfigurations Found:")
        for misconfiguration in misconfigurations:
            print(misconfiguration)

    for failed in failed_files:
        print(f"Not scanned: {failed}")

    # I write the output file only when there is something to put
    # in it. A clean scan leaves no file behind, so a missing
    # output file after a run means clean, not lost.
    if args.output and (misconfigurations or failed_files):
        try:
            with open(args.output, "w") as outfile:
                for misconfiguration in misconfigurations:
                    outfile.write(misconfiguration + "\n")
                for failed in failed_files:
                    outfile.write(f"Not scanned: {failed}\n")
            print(f"Results saved to: {args.output}")
        except Exception as e:
            logging.error(f"Error writing to output file: {e}")

    if misconfigurations or failed_files:
        sys.exit(1)  # Indicate that vulnerabilities were found, or a file was not scanned
    else:
        print("No JWT misconfigurations found.")
        sys.exit(0)  # Indicate successful scan, no issues found


if __name__ == "__main__":
    main()