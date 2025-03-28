import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from dotenv import load_dotenv
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

load_dotenv()

def send_email_sendgrid(to_email, subject, content):
    message = Mail(
        from_email='aiexpert0208@gmail.com',
        to_emails=to_email,
        subject=subject,
        html_content=content
    )
    try:
        sg = SendGridAPIClient(os.getenv('SENDGRID_API_KEY'))
        response = sg.send(message)
        print(f"Email sent successfully to {to_email}")

    except Exception as e:
        print(f"Error sending email to {to_email}: {e}")
        raise e

def send_email_smtp(to_email, subject, content):
    message = MIMEMultipart()
    message['From'] = 'aiexpert0208@gmail.com'
    message['To'] = to_email
    message['Subject'] = subject
    message.attach(MIMEText(content, 'plain'))


    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_PASSWORD'))
        server.sendmail(os.getenv('EMAIL_USER'), to_email, message.as_string())
        server.quit()
        print(f"Email sent successfully to {to_email}")

    except Exception as e:
        print(f"Error sending email to {to_email}: {e}")
        raise e
    
    
    
    