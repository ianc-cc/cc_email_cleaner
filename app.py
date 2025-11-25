import streamlit as st
import pandas as pd
import re
import dns.resolver
import time
from io import StringIO, BytesIO
import base64
import smtplib
import socket
from zipfile import ZipFile

# List of common disposable email domains
DISPOSABLE_DOMAINS = {
    'mailinator.com', 'guerrillamail.com', 'temp-mail.org', 'throwaway.email',
    '10minutemail.com', 'trashmail.com', 'tempmail.com', 'yopmail.com',
    'maildrop.cc', 'mohmal.com', 'sharklasers.com', 'guerrillamailblock.com',
    'grr.la', 'spam4.me', 'emailondeck.com', 'fakeinbox.com'
}

def validate_email_syntax(email):
    """Validate email syntax using regex"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, str(email)) is not None

def check_mx_record(domain):
    """Check if domain has MX records"""
    try:
        dns.resolver.resolve(domain, 'MX')
        return True
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, Exception):
        return False

def is_disposable_email(email):
    """Check if email is from a disposable email provider"""
    try:
        domain = email.split('@')[1].lower()
        return domain in DISPOSABLE_DOMAINS
    except:
        return False

def verify_smtp(email, timeout=10):
    """
    Verify email exists via SMTP connection
    Returns: (is_valid, reason)
    """
    try:
        # Extract domain
        domain = email.split('@')[1]
        
        # Get MX records
        try:
            mx_records = dns.resolver.resolve(domain, 'MX')
            mx_host = str(mx_records[0].exchange)
        except:
            return False, "No MX record"
        
        # Connect to SMTP server
        server = smtplib.SMTP(timeout=timeout)
        server.set_debuglevel(0)
        
        # Connect to MX host
        server.connect(mx_host)
        server.helo(server.local_hostname)
        server.mail('verify@example.com')
        code, message = server.rcpt(email)
        server.quit()
        
        # Check response code
        if code == 250:
            return True, "Valid"
        else:
            return False, "SMTP rejected"
            
    except smtplib.SMTPServerDisconnected:
        return None, "Server disconnected"
    except smtplib.SMTPConnectError:
        return None, "Connection failed"
    except socket.timeout:
        return None, "Timeout"
    except Exception as e:
        return None, f"SMTP check failed"

def validate_email(email, seen_emails, use_smtp=False, rate_limit_delay=1.5):
    """
    Comprehensive email validation
    Returns: (is_valid, reason)
    """
    email_str = str(email).strip().lower()
    
    # Check for empty or NaN
    if pd.isna(email) or email_str == '' or email_str == 'nan':
        return False, "Empty email"
    
    # Check for duplicates
    if email_str in seen_emails:
        return False, "Duplicate"
    
    # Syntax validation
    if not validate_email_syntax(email_str):
        return False, "Invalid syntax"
    
    # Check for disposable emails
    if is_disposable_email(email_str):
        return False, "Disposable email"
    
    # Extract domain
    try:
        domain = email_str.split('@')[1]
    except:
        return False, "Invalid format"
    
    # Check MX records
    if not check_mx_record(domain):
        return False, "No MX record"
    
    # SMTP verification (optional, slower)
    if use_smtp:
        time.sleep(rate_limit_delay)  # Rate limiting
        smtp_valid, smtp_reason = verify_smtp(email_str)
        if smtp_valid is False:
            return False, smtp_reason
        # If smtp_valid is None (connection error), continue without SMTP result
    
    return True, "Valid"

def process_csv(df, progress_bar, status_text, use_smtp=False):
    """Process the CSV and validate emails"""
    total_rows = len(df)
    seen_emails = set()
    statuses = []
    
    start_time = time.time()
    
    for idx, row in df.iterrows():
        # Calculate progress
        progress = (idx + 1) / total_rows
        progress_bar.progress(progress)
        
        # Estimate time remaining
        elapsed = time.time() - start_time
        if idx > 0:
            avg_time_per_row = elapsed / (idx + 1)
            remaining_rows = total_rows - (idx + 1)
            eta_seconds = avg_time_per_row * remaining_rows
            smtp_status = " (with SMTP)" if use_smtp else ""
            eta_text = f"Processing{smtp_status}... {idx + 1}/{total_rows} | ETA: {eta_seconds:.1f}s"
        else:
            eta_text = f"Processing... {idx + 1}/{total_rows}"
        
        status_text.text(eta_text)
        
        # Validate email
        email = row['Email Address']
        is_valid, reason = validate_email(email, seen_emails, use_smtp=use_smtp)
        
        if is_valid:
            seen_emails.add(str(email).strip().lower())
            statuses.append('Valid')
        else:
            statuses.append(f'Invalid ({reason})')
    
    # Add Status column
    df['Status'] = statuses
    
    # Calculate stats
    valid_count = sum(1 for s in statuses if s == 'Valid')
    invalid_count = total_rows - valid_count
    
    return df, valid_count, invalid_count

def create_download_zip(df):
    """Generate a ZIP file with full results, valid emails, and invalid emails"""
    # Create a BytesIO buffer for the ZIP
    zip_buffer = BytesIO()
    
    with ZipFile(zip_buffer, 'w') as zip_file:
        # Full results CSV
        full_csv = df.to_csv(index=False)
        zip_file.writestr('full_results.csv', full_csv)
        
        # Valid emails only CSV
        valid_df = df[df['Status'] == 'Valid']
        valid_csv = valid_df.to_csv(index=False)
        zip_file.writestr('valid_emails.csv', valid_csv)
        
        # Invalid emails only CSV
        invalid_df = df[df['Status'] != 'Valid']
        invalid_csv = invalid_df.to_csv(index=False)
        zip_file.writestr('invalid_emails.csv', invalid_csv)
    
    zip_buffer.seek(0)
    return zip_buffer

def get_download_link(zip_buffer):
    """Generate a download link for the ZIP file"""
    b64 = base64.b64encode(zip_buffer.read()).decode()
    return f'<a href="data:application/zip;base64,{b64}" download="email_results.zip">Download Results (ZIP)</a>'

# Streamlit App
st.set_page_config(page_title="Email Cleaner", page_icon="✉️", layout="wide")

st.title("✉️ Email List Cleaner")
st.markdown("Upload your CSV file to validate and clean email addresses")

# File uploader
uploaded_file = st.file_uploader("Choose a CSV file", type=['csv'])

if uploaded_file is not None:
    # Read CSV
    df = pd.read_csv(uploaded_file)
    
    # Keep only the required columns
    required_columns = ['Name', 'Email Address', 'Mobile Number']
    # Filter to only columns that exist in both the dataframe and required list
    columns_to_keep = [col for col in required_columns if col in df.columns]
    df = df[columns_to_keep]
    
    # Validate required column exists
    if 'Email Address' not in df.columns:
        st.error("❌ CSV must contain 'Email Address' column!")
    else:
        st.success(f"✅ File uploaded successfully! Found {len(df)} rows")
        
        # Show preview of original data
        st.subheader("📋 Preview of Original Data")
        st.dataframe(df.head(10), use_container_width=True)
        
        # SMTP verification option
        use_smtp = st.checkbox(
            "🔍 Enable SMTP Verification (More accurate but slower - adds ~1.5s per email)",
            value=False,
            help="Connects to mail servers to verify if specific email addresses exist. This is more thorough but significantly slower."
        )
        
        if use_smtp:
            st.warning("⚠️ SMTP verification enabled. Processing will be significantly slower but more accurate.")
        
        # Run validation button
        if st.button("🚀 Clean Email List", type="primary"):
            st.subheader("⚙️ Processing...")
            
            # Create progress bar and status text
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            # Process the CSV
            start_time = time.time()
            cleaned_df, valid_count, invalid_count = process_csv(df.copy(), progress_bar, status_text, use_smtp=use_smtp)
            processing_time = time.time() - start_time
            
            # Clear progress indicators
            progress_bar.empty()
            status_text.empty()
            
            # Show results
            st.success(f"✅ Processing complete in {processing_time:.2f} seconds!")
            
            # Stats
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Emails", len(df))
            with col2:
                st.metric("Valid Emails", valid_count, delta=None)
            with col3:
                st.metric("Removed", invalid_count, delta=f"-{invalid_count}")
            
            # Show preview of cleaned data
            st.subheader("📊 Preview of Cleaned Data")
            st.dataframe(cleaned_df.head(20), use_container_width=True)
            
            # Download button
            st.subheader("💾 Download Results")
            zip_buffer = create_download_zip(cleaned_df)
            st.markdown(get_download_link(zip_buffer), unsafe_allow_html=True)
            
            st.info("""
            📦 **ZIP file contains 3 CSV files:**
            - `full_results.csv` - All emails with Status column
            - `valid_emails.csv` - Only valid emails
            - `invalid_emails.csv` - Only invalid emails
            """)

else:
    st.info("👆 Please upload a CSV file to get started")
    
    # Show example format
    st.subheader("📝 Expected CSV Format")
    example_df = pd.DataFrame({
        'Name': ['John Doe', 'Jane Smith'],
        'Email Address': ['john@example.com', 'jane@company.com'],
        'Mobile Number': ['555-0100', '555-0200']
    })
    st.dataframe(example_df, use_container_width=True)