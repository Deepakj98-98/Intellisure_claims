# IntelliSure AI: Claims Exception Resolution Platform MVP

IntelliSure AI is a production-grade multi-agent Insurance Claims Exception Resolution Platform. The application showcases an advanced AI workflow using **Amazon Bedrock Agents** to orchestrate claim intake, policy validation, decision adjudication, and compliance auditing in a unified, automated pipeline.

---

## 🛠️ Architecture Overview

1. **PDF Text Extraction**: Extracts text from user-uploaded PDFs using `PyMuPDF (fitz)`.
2. **S3 Storage**: Uploads original PDFs to Amazon S3 under the `claims/` directory.
3. **Sequential Multi-Agent Pipeline**:
   - **Claim Intake Agent**: Parses raw text, returns structured claims details.
   - **Policy Validation Agent**: Validates claims details against insurance policies and flags discrepancies.
   - **Adjudication Agent**: Takes both claim and validation details, determining approval/denial exceptions and payment amounts.
   - **Audit Agent**: Audits the final decision for quality assurance and compliance risk scoring.
4. **Database Storage**: Persists the combined results into Amazon DynamoDB.
5. **Interactive Frontend**: Built on React + Vite + TailwindCSS, featuring a drag-and-drop zone, progressive stage stepper, and formatted/JSON dashboards.

---

## 📋 Environment Variables

Create a `.env` file in the root folder using `.env.example` as a template:

```env
# AWS Credentials and Region
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-aws-access-key-id
AWS_SECRET_ACCESS_KEY=your-aws-secret-access-key

# Storage resources
BUCKET_NAME=your-s3-claims-bucket-name
CLAIMS_TABLE=your-dynamodb-claims-table-name

# Agent 1: Intake
CLAIM_AGENT_ID=your-claim-intake-agent-id
CLAIM_AGENT_ALIAS=your-claim-intake-agent-alias-id

# Agent 2: Policy Validation
POLICY_AGENT_ID=your-policy-validation-agent-id
POLICY_AGENT_ALIAS=your-policy-validation-agent-alias-id

# Agent 3: Adjudication
ADJUDICATION_AGENT_ID=your-adjudication-agent-id
ADJUDICATION_AGENT_ALIAS=your-adjudication-agent-alias-id

# Agent 4: Audit QA
AUDIT_AGENT_ID=your-audit-agent-id
AUDIT_AGENT_ALIAS=your-audit-agent-alias-id

# Frontend API URL configuration
VITE_API_URL=http://localhost:8000
```

---

## 🚀 Running Locally

### Option A: Using Docker Compose (Recommended)

To spin up both the backend and frontend simultaneously with hot-reloading:

```bash
# 1. Place your .env variables in the root directory
# 2. Spin up containers
docker-compose up --build
```
- Backend runs at: `http://localhost:8000`
- Frontend dashboard runs at: `http://localhost:5173`

---

### Option B: Running Services Manually

#### 1. Start the Backend
Make sure you have Python 3.12 installed:

```bash
cd backend
python -m venv venv
# Windows:
.\venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run server (set env vars beforehand or load them)
python app.py
```
Backend runs at `http://localhost:8000`.

#### 2. Start the Frontend
Make sure you have Node.js 18+ installed:

```bash
cd frontend
npm install
npm run dev
```
Frontend runs at `http://localhost:5173`.

---

## 🔌 API Endpoints

### 1. Health Verification
- **Endpoint**: `GET /health`
- **Output**:
  ```json
  {
      "status": "healthy"
  }
  ```

### 2. File Upload and Multi-Agent Pipeline
- **Endpoint**: `POST /upload`
- **Request Type**: `multipart/form-data`
- **Form Field**: `file` (Must be a `.pdf` document)
- **Output**:
  ```json
  {
      "claim": {
          "claim_id": "...",
          "patient_name": "...",
          "total_amount": 1200.00,
          "...": "..."
      },
      "policy": {
          "is_active": true,
          "coverage_amount": 1000.00,
          "discrepancies": []
      },
      "decision": {
          "status": "APPROVED",
          "adjudicated_amount": 1000.00,
          "exception_notes": "..."
      },
      "audit": {
          "qa_status": "PASSED",
          "risk_score": "LOW",
          "compliance_flags": []
      }
  }
  ```

---

## ☁️ Deployment Instructions

### 1. Deploying the Backend to AWS App Runner

AWS App Runner provides a fully managed containerized service perfect for hosting the FastAPI backend.

#### Step 1: Push Container to Amazon ECR
1. Create a private repository in Amazon ECR named `intellisure-backend`.
2. Authenticate Docker with ECR:
   ```bash
   aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <YOUR_AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com
   ```
3. Build and tag the Docker image:
   ```bash
   docker build -t intellisure-backend .
   docker tag intellisure-backend:latest <YOUR_AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/intellisure-backend:latest
   ```
4. Push to ECR:
   ```bash
   docker push <YOUR_AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/intellisure-backend:latest
   ```

#### Step 2: Create App Runner Service
1. Open the **AWS App Runner** console and click **Create Service**.
2. Select **Container registry** and choose **Amazon ECR**.
3. Browse for the `intellisure-backend:latest` image.
4. Under **Deployment settings**, choose **Automatic** or **Manual**.
5. In **Configuration**:
   - Set **Port** to `8000`.
   - Add the necessary Environment Variables (from your `.env` file).
   - Ensure the service's IAM instance role has policies for `AmazonS3FullAccess`, `AmazonDynamoDBFullAccess`, and `AmazonBedrockFullAccess` (or custom minimal permission policies targeting your specific resources).
6. Click **Create & Deploy**. App Runner will generate a public URL (e.g. `https://xxxxxx.us-east-1.awsapprunner.com`).

---

### 2. Deploying the Frontend to AWS Amplify

AWS Amplify Hosting is a fully managed service for deploying Vite static websites.

#### Step 1: Push Frontend Code to GitHub
Ensure your code is pushed to a Git provider supported by Amplify (GitHub, GitLab, Bitbucket).

#### Step 2: Connect Repo in Amplify Console
1. Open the **AWS Amplify** console and click **New App** > **Host web app**.
2. Connect your Git repository provider and select your repository and branch.
3. Amplify will auto-detect the Vite build settings. Configure the App Build Settings block to target the frontend subdirectory:
   ```yaml
   version: 1
   frontend:
     phases:
       preBuild:
         commands:
           - cd frontend
           - npm ci
       build:
         commands:
           - npm run build
     artifacts:
       baseDirectory: frontend/dist
       files:
         - '**/*'
     cache:
       paths:
         - frontend/node_modules/**/*
   ```
4. Add the environment variable `VITE_API_URL` under **Advanced Settings** and set its value to your AWS App Runner public URL (e.g. `https://xxxxxx.us-east-1.awsapprunner.com`).
5. Click **Save and Deploy**. Once the deployment pipeline completes, Amplify will generate a secure `https://xxx.amplifyapp.com` domain for your dashboard.
