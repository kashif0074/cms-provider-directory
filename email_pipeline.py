import os
import re
import socket
import ipaddress
from urllib.parse import urlparse
import requests
from dotenv import load_dotenv
from search_service import search_web, SearchError

load_dotenv()

# Regex for email extraction
EMAIL_REGEX = re.compile(
    r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
)

# Common non-email or noise extensions and patterns
IGNORED_EXTENSIONS = (
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.tiff',
    '.css', '.js', '.woff', '.woff2', '.ttf', '.eot', '.ico'
)

IGNORED_DOMAINS = {
    'example.com', 'domain.com', 'yourdomain.com', 'email.com',
    'sentry.io', 'w3.org', 'schemas.microsoft.com', 'wixpress.com',
    'wordpress.com', 'cloudflare.com', 'google.com', 'apple.com',
    'github.com', 'gravatar.com', 'schema.org', 'adobe.com'
}

ORG_EMAIL_PREFIXES = {
    'info', 'contact', 'admin', 'support', 'appointments', 'office',
    'billing', 'help', 'inquiries', 'frontdesk', 'referrals', 'scheduling',
    'patientservices', 'general', 'hello', 'feedback', 'service', 'inquiry',
    'reception', 'care', 'customer', 'custserv', 'press', 'media', 'compliance'
}

# Directories and aggregators that should not be classified as the provider's official website
AGGREGATOR_DOMAINS = {
    'healthgrades.com', 'vitals.com', 'webmd.com', 'usnews.com', 'doximity.com',
    'sharecare.com', 'zocdoc.com', 'youtube.com', 'facebook.com', 'twitter.com',
    'x.com', 'instagram.com', 'medicare.gov', 'npidb.org', 'npino.com',
    'linkedin.com', 'wikipedia.org', 'yelp.com', 'mapquest.com', 'yellowpages.com',
    'healthcare4ppl.com', 'caredash.com', 'topnpi.com', 'medicalnewstoday.com',
    'castlighthealth.com', 'docspot.com', 'healthjournal.com'
}


def is_safe_url(url_str: str) -> bool:
    """
    Validates URL to block localhost, private networks, and non-HTTP schemes (SSRF protection).
    """
    try:
        parsed = urlparse(url_str)
        if parsed.scheme not in ('http', 'https'):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        if hostname.lower() in ('localhost', '127.0.0.1', '::1'):
            return False
            
        addr_info = socket.getaddrinfo(hostname, None)
        for item in addr_info:
            ip_str = item[4][0]
            ip_obj = ipaddress.ip_address(ip_str)
            if (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_multicast
                or ip_obj.is_reserved
            ):
                return False
        return True
    except Exception:
        return False


def clean_linkedin_url(url: str) -> str:
    """
    Cleans and canonicalizes a LinkedIn profile URL (e.g. removes tracking query params).
    """
    try:
        parsed = urlparse(url)
        if "linkedin.com" in parsed.netloc and "/in/" in parsed.path:
            parts = [p for p in parsed.path.strip("/").split("/") if p]
            if len(parts) >= 2 and parts[0] == "in":
                return f"https://www.linkedin.com/in/{parts[1]}"
    except Exception:
        pass
    return url


def is_official_website(url: str) -> bool:
    """
    Checks if a URL is an official provider practice or hospital/clinic website
    rather than an aggregator or social network directory.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return False
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        for agg in AGGREGATOR_DOMAINS:
            if agg in domain:
                return False
        return True
    except Exception:
        return False


def verify_person_match(text: str, first_name: str, last_name: str) -> bool:
    """
    Checks if the given text, URL, or title mentions the provider's name tokens.
    """
    target = text.lower()
    fn = (first_name or "").strip().lower()
    ln = (last_name or "").strip().lower()

    if ln and len(ln) >= 3 and ln in target:
        return True
    if fn and len(fn) >= 3 and fn in target:
        return True
    return False


def extract_content_tavily(urls):
    """
    Extracts webpage markdown content using Tavily Extract API.
    """
    key = os.getenv("TAVILY_API_KEY")
    if not key or not urls:
        return {}

    safe_urls = [u for u in urls if is_safe_url(u)][:5]
    if not safe_urls:
        return {}

    try:
        resp = requests.post(
            "https://api.tavily.com/extract",
            headers={"Authorization": f"Bearer {key}"},
            json={"urls": safe_urls},
            timeout=(10, 25),
        )
        if resp.status_code == 200:
            data = resp.json()
            results = {}
            for item in data.get("results", []):
                url = item.get("url")
                content = item.get("raw_content", "")
                if url and content:
                    results[url] = content
            return results
    except Exception:
        pass
    return {}


def safe_fetch_page(url: str, timeout=10, max_bytes=500_000) -> str:
    """
    Direct HTTP fetch fallback with strict safety constraints.
    """
    if not is_safe_url(url):
        return ""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        with requests.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=True) as r:
            if not is_safe_url(r.url):
                return ""
            if r.status_code != 200:
                return ""
            content_type = r.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type and "text/plain" not in content_type:
                return ""
            content = b""
            for chunk in r.iter_content(chunk_size=8192):
                content += chunk
                if len(content) > max_bytes:
                    break
            return content.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def clean_extracted_emails(text: str) -> list:
    """
    Finds valid emails and discards junk/image filenames and dummy addresses.
    """
    candidates = EMAIL_REGEX.findall(text)
    cleaned = []
    seen = set()

    for email in candidates:
        email_clean = email.strip().strip(".,;:()[]{}<>\"'")
        email_lower = email_clean.lower()

        if email_lower in seen:
            continue
        if any(email_lower.endswith(ext) for ext in IGNORED_EXTENSIONS):
            continue
        
        parts = email_lower.split("@")
        if len(parts) != 2:
            continue
        local_part, domain_part = parts

        if domain_part in IGNORED_DOMAINS:
            continue
        if len(local_part) < 2 or len(domain_part) < 4:
            continue
        if "." not in domain_part:
            continue
            
        seen.add(email_lower)
        cleaned.append(email_clean)

    return cleaned


def find_evidence_snippet(text: str, email: str, window=160) -> str:
    """
    Extracts a contextual snippet surrounding the email in text.
    """
    idx = text.find(email)
    if idx == -1:
        idx = text.lower().find(email.lower())
    if idx == -1:
        return ""
    start = max(0, idx - window)
    end = min(len(text), idx + len(email) + window)
    snippet = text[start:end].replace("\r", " ").replace("\n", " ").strip()
    return f"...{snippet}..."


def evaluate_email_attribution(email: str, text: str, first_name: str, last_name: str) -> str:
    """
    Classifies email attribution:
    - 'Found': explicitly attributed to provider (matches provider name or bio context)
    - 'Organization Contact Found': clinic / hospital general address
    - 'Needs Review': email present, but relationship is ambiguous
    """
    local_part = email.split("@")[0].lower()
    fn = (first_name or "").strip().lower()
    ln = (last_name or "").strip().lower()

    is_org_prefix = any(local_part.startswith(p) for p in ORG_EMAIL_PREFIXES)

    name_in_handle = False
    if ln and len(ln) >= 3 and ln in local_part:
        name_in_handle = True
    elif fn and len(fn) >= 3 and fn in local_part:
        name_in_handle = True

    if name_in_handle:
        return "Found"

    if is_org_prefix:
        return "Organization Contact Found"

    snippet = find_evidence_snippet(text, email, window=200).lower()
    has_fn = fn and len(fn) >= 3 and fn in snippet
    has_ln = ln and len(ln) >= 3 and ln in snippet

    if has_fn and has_ln:
        return "Found"
    elif has_ln or has_fn:
        return "Needs Review"

    return "Needs Review"


def search_provider_contact_workflow(provider_info: dict) -> dict:
    """
    Implements the requested 5-step provider contact search workflow:
    1. Search by Provider Name: Search Google/Tavily for official website, professional profile, or public work email.
    2. Search Associated Hospitals and Clinics: Search associated Medicare hospital/clinic website to confirm affiliation and extract email.
    3. Search Provider's Own Website: Search provider's practice website / contact page for email and contact info.
    4. Search LinkedIn: Search LinkedIn for professional profile URL and publicly listed email.
    5. Display the Results: Return provider name, email, associated hospital/clinic, official website URL,
       LinkedIn profile URL, source URL, email status ('Found' or 'Not Found').
    """
    stages_log = []

    first_name = provider_info.get("first_name", "").strip()
    last_name = provider_info.get("last_name", "").strip()
    full_name = provider_info.get("name", "").strip()
    if not full_name and (first_name or last_name):
        full_name = f"{first_name} {last_name}".strip()

    facility = provider_info.get("facility", "").strip() or provider_info.get("facility_name", "").strip()
    city = provider_info.get("city", "").strip() or provider_info.get("citytown", "").strip()
    state = provider_info.get("state", "").strip()
    specialty = provider_info.get("specialty", "").strip() or provider_info.get("pri_spec", "").strip()

    if not full_name:
        return {
            "provider_name": "Unknown",
            "email_status": "Not Found",
            "email": None,
            "associated_hospital_or_clinic": facility or "Not Specified",
            "official_website_url": None,
            "linkedin_url": None,
            "source_url": None,
            "evidence_snippet": None,
            "stages_log": ["Error: Provider name is required."],
            "error": "Provider name is required"
        }

    clean_name = full_name.replace('"', '').strip()
    associated_facility = facility if facility else "Not Specified in Medicare Record"

    official_website_url = None
    linkedin_url = None
    discovered_email = None
    email_source_url = None
    email_evidence_snippet = None

    # -------------------------------------------------------------
    # Step 1: Search by Provider Name
    # -------------------------------------------------------------
    q1 = f'"{clean_name}" {city} {state} {specialty} official profile OR website'.strip()
    stages_log.append(f"Step 1 [Search by Provider Name]: Executing search for '{clean_name}'")
    try:
        results_s1 = search_web(q1)
        stages_log.append(f"Step 1: Found {len(results_s1)} search results")
        
        for r in results_s1:
            u = r.get("url", "")
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            
            # Check for LinkedIn profile
            if "linkedin.com/in/" in u and not linkedin_url:
                if verify_person_match(u + " " + title, first_name, last_name):
                    linkedin_url = clean_linkedin_url(u)
                    stages_log.append(f"Step 1: Captured LinkedIn profile -> {linkedin_url}")

            # Check for official website or physician profile
            if is_official_website(u) and not official_website_url:
                if verify_person_match(u + " " + title + " " + snippet, first_name, last_name):
                    official_website_url = u
                    stages_log.append(f"Step 1: Identified official provider profile/website -> {official_website_url}")

            # Check snippet for email
            if not discovered_email:
                emails_in_snippet = clean_extracted_emails(snippet)
                for em in emails_in_snippet:
                    attr = evaluate_email_attribution(em, snippet, first_name, last_name)
                    if attr in ("Found", "Organization Contact Found"):
                        discovered_email = em
                        email_source_url = u
                        email_evidence_snippet = find_evidence_snippet(snippet, em)
                        stages_log.append(f"Step 1: Extracted public email from search snippet -> {discovered_email}")
                        break

    except Exception as e:
        stages_log.append(f"Step 1 Notice: {str(e)}")

    # -------------------------------------------------------------
    # Step 2: Search Associated Hospitals and Clinics
    # -------------------------------------------------------------
    if facility:
        q2 = f'"{facility}" {city} {state} official website physician "{clean_name}"'.strip()
        stages_log.append(f"Step 2 [Search Associated Hospital/Clinic]: Checking '{facility}' for '{clean_name}'")
        try:
            results_s2 = search_web(q2)
            stages_log.append(f"Step 2: Found {len(results_s2)} facility results")

            facility_profile_urls = []
            for r in results_s2:
                u = r.get("url", "")
                title = r.get("title", "")
                snippet = r.get("snippet", "")

                # If we don't have an official website yet, or if this is the confirmed hospital provider profile
                if is_official_website(u):
                    if verify_person_match(u + " " + title + " " + snippet, first_name, last_name):
                        if not official_website_url or ("provider" in u.lower() or "doctor" in u.lower()):
                            official_website_url = u
                            stages_log.append(f"Step 2: Associated hospital official profile -> {official_website_url}")
                        facility_profile_urls.append(u)

                # Check snippet for email
                if not discovered_email:
                    emails_in_snippet = clean_extracted_emails(snippet)
                    for em in emails_in_snippet:
                        attr = evaluate_email_attribution(em, snippet, first_name, last_name)
                        if attr in ("Found", "Organization Contact Found"):
                            discovered_email = em
                            email_source_url = u
                            email_evidence_snippet = find_evidence_snippet(snippet, em)
                            stages_log.append(f"Step 2: Extracted public email from hospital snippet -> {discovered_email}")
                            break

            # If no email yet, extract page content from the hospital provider profile
            if not discovered_email and facility_profile_urls:
                target_url = facility_profile_urls[0]
                stages_log.append(f"Step 2: Inspecting hospital profile page content -> {target_url}")
                extracted = extract_content_tavily([target_url])
                page_text = extracted.get(target_url) or safe_fetch_page(target_url)
                if page_text:
                    page_emails = clean_extracted_emails(page_text)
                    for em in page_emails:
                        attr = evaluate_email_attribution(em, page_text, first_name, last_name)
                        if attr in ("Found", "Organization Contact Found"):
                            discovered_email = em
                            email_source_url = target_url
                            email_evidence_snippet = find_evidence_snippet(page_text, em)
                            stages_log.append(f"Step 2: Extracted public professional email from hospital profile page -> {discovered_email}")
                            break

        except Exception as e:
            stages_log.append(f"Step 2 Notice: {str(e)}")
    else:
        stages_log.append("Step 2: Skipped (no associated hospital or clinic in Medicare record)")

    # -------------------------------------------------------------
    # Step 3: Search the Provider's Own Website
    # -------------------------------------------------------------
    if not discovered_email:
        q3 = f'"{clean_name}" {city} {state} official website OR clinic OR practice contact'.strip()
        stages_log.append(f"Step 3 [Search Provider's Own Website]: Searching for practice website and contact page")
        try:
            results_s3 = search_web(q3)
            stages_log.append(f"Step 3: Found {len(results_s3)} practice results")
            
            practice_urls = []
            for r in results_s3:
                u = r.get("url", "")
                title = r.get("title", "")
                snippet = r.get("snippet", "")

                if is_official_website(u):
                    if verify_person_match(u + " " + title + " " + snippet, first_name, last_name):
                        if not official_website_url:
                            official_website_url = u
                            stages_log.append(f"Step 3: Discovered provider's practice website -> {official_website_url}")
                        practice_urls.append(u)

                # Check snippet
                if not discovered_email:
                    emails_in_snippet = clean_extracted_emails(snippet)
                    for em in emails_in_snippet:
                        attr = evaluate_email_attribution(em, snippet, first_name, last_name)
                        if attr in ("Found", "Organization Contact Found"):
                            discovered_email = em
                            email_source_url = u
                            email_evidence_snippet = find_evidence_snippet(snippet, em)
                            stages_log.append(f"Step 3: Extracted email from practice snippet -> {discovered_email}")
                            break

            # Inspect practice page / contact page if no email yet
            if not discovered_email and practice_urls:
                target_url = practice_urls[0]
                stages_log.append(f"Step 3: Reading provider website content for contact details -> {target_url}")
                extracted = extract_content_tavily([target_url])
                page_text = extracted.get(target_url) or safe_fetch_page(target_url)
                if page_text:
                    page_emails = clean_extracted_emails(page_text)
                    for em in page_emails:
                        attr = evaluate_email_attribution(em, page_text, first_name, last_name)
                        if attr in ("Found", "Organization Contact Found"):
                            discovered_email = em
                            email_source_url = target_url
                            email_evidence_snippet = find_evidence_snippet(page_text, em)
                            stages_log.append(f"Step 3: Extracted email from provider's practice page -> {discovered_email}")
                            break

        except Exception as e:
            stages_log.append(f"Step 3 Notice: {str(e)}")
    else:
        stages_log.append("Step 3: Skipped (email already confirmed in earlier step)")

    # -------------------------------------------------------------
    # Step 4: Search LinkedIn
    # -------------------------------------------------------------
    # Ensure LinkedIn profile is captured
    if not linkedin_url:
        q4 = f'site:linkedin.com/in/ "{clean_name}" {city} {state} {specialty}'.strip()
        stages_log.append(f"Step 4 [Search LinkedIn]: Looking up professional profile on LinkedIn")
        try:
            results_s4 = search_web(q4)
            for r in results_s4:
                u = r.get("url", "")
                title = r.get("title", "")
                snippet = r.get("snippet", "")
                if "linkedin.com/in/" in u:
                    if verify_person_match(u + " " + title, first_name, last_name):
                        linkedin_url = clean_linkedin_url(u)
                        stages_log.append(f"Step 4: Retrieved LinkedIn profile URL -> {linkedin_url}")
                        
                        # Check snippet for publicly listed email
                        if not discovered_email:
                            emails_in_snippet = clean_extracted_emails(snippet)
                            for em in emails_in_snippet:
                                attr = evaluate_email_attribution(em, snippet, first_name, last_name)
                                if attr in ("Found", "Organization Contact Found"):
                                    discovered_email = em
                                    email_source_url = u
                                    email_evidence_snippet = find_evidence_snippet(snippet, em)
                                    stages_log.append(f"Step 4: Extracted public email from LinkedIn snippet -> {discovered_email}")
                                    break
                        break
            if not linkedin_url:
                stages_log.append("Step 4: No matching public LinkedIn profile identified")
        except Exception as e:
            stages_log.append(f"Step 4 Notice: {str(e)}")
    else:
        stages_log.append(f"Step 4: LinkedIn profile already obtained -> {linkedin_url}")

    # -------------------------------------------------------------
    # Step 5: Synthesize and Display Results
    # -------------------------------------------------------------
    email_status = "Found" if discovered_email else "Not Found"
    stages_log.append(f"Step 5 [Results Formulation]: Email Status -> {email_status}")

    return {
        "provider_name": full_name,
        "email_status": email_status,
        "email": discovered_email,
        "associated_hospital_or_clinic": associated_facility,
        "official_website_url": official_website_url,
        "linkedin_url": linkedin_url,
        "source_url": email_source_url,
        "evidence_snippet": email_evidence_snippet,
        "stages_log": stages_log,
    }


# Retain run_provider_pipeline for backwards compatibility with existing route
def run_provider_pipeline(provider_info: dict) -> dict:
    return search_provider_contact_workflow(provider_info)
