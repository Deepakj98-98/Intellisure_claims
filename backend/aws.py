import os
from dotenv import load_dotenv
import boto3
from datetime import datetime
import logging

# Load environment variables from a local .env file, if present
load_dotenv()

logger = logging.getLogger(__name__)

# Config from environment variables
BUCKET_NAME = os.getenv("BUCKET_NAME")
CLAIMS_TABLE = os.getenv("CLAIMS_TABLE")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Initialize boto3 clients
# Note: In production, boto3 will automatically pick up AWS credentials 
# from the environment or IAM Instance Profiles.
try:
    s3_client = boto3.client("s3", region_name=AWS_REGION)
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
except Exception as e:
    logger.error(f"Failed to initialize AWS clients: {str(e)}")
    s3_client = None
    dynamodb = None

def upload_claim_pdf(file_bytes: bytes, filename: str) -> str:
    """
    Uploads original claim PDF bytes to S3 in the claims/ folder.
    
    Args:
        file_bytes (bytes): The raw file bytes.
        filename (str): The name of the file to save as.
        
    Returns:
        str: S3 URI of the uploaded file.
    """
    if not BUCKET_NAME:
        logger.error("S3 upload failed: BUCKET_NAME environment variable is not set.")
        raise ValueError("BUCKET_NAME environment variable is not set.")
        
    if not s3_client:
        logger.error("S3 client is not initialized.")
        raise RuntimeError("AWS credentials or configuration is missing.")

    # Standardize folder prefix
    key = f"claims/{filename}"
    logger.info(f"Uploading file to S3 bucket '{BUCKET_NAME}' with key '{key}'...")
    
    try:
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=key,
            Body=file_bytes,
            ContentType="application/pdf"
        )
        s3_uri = f"s3://{BUCKET_NAME}/{key}"
        logger.info(f"Successfully uploaded PDF to S3: {s3_uri}")
        return s3_uri
    except Exception as e:
        logger.error(f"S3 upload operation failed: {str(e)}")
        raise e

def save_claim_resolution(
    claim_id: str,
    claim_data: dict,
    policy_data: dict,
    decision_data: dict,
    audit_data: dict,
    status: str = "RESOLVED"
) -> dict:
    """
    Saves the full resolution result of the claim workflow into DynamoDB.
    
    Args:
        claim_id (str): Unique partition key ClaimID.
        claim_data (dict): Parsed claim details from Intake agent.
        policy_data (dict): Validation details from Policy agent.
        decision_data (dict): Decision details from Adjudication agent.
        audit_data (dict): Final audit review details from Audit agent.
        status (str): Current status of the claim (defaults to 'RESOLVED').
        
    Returns:
        dict: The item stored in DynamoDB.
    """
    if not CLAIMS_TABLE:
        logger.error("DynamoDB save failed: CLAIMS_TABLE environment variable is not set.")
        raise ValueError("CLAIMS_TABLE environment variable is not set.")
        
    if not dynamodb:
        logger.error("DynamoDB resource is not initialized.")
        raise RuntimeError("AWS credentials or configuration is missing.")

    logger.info(f"Saving claim record '{claim_id}' to DynamoDB table '{CLAIMS_TABLE}'...")
    try:
        table = dynamodb.Table(CLAIMS_TABLE)
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        
        item = {
            "ClaimID": claim_id,
            "Timestamp": timestamp,
            "Claim": claim_data,
            "Policy": policy_data,
            "Decision": decision_data,
            "Audit": audit_data,
            "Status": status
        }
        
        table.put_item(Item=item)
        logger.info(f"Successfully stored claim record in DynamoDB: {claim_id}")
        return item
    except Exception as e:
        logger.error(f"DynamoDB put_item failed: {str(e)}")
        raise e
