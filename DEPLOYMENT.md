# IntelliSure AI: Deployment & Infrastructure Configuration Guide

This guide provides step-by-step instructions for setting up the required AWS infrastructure and deploying the IntelliSure AI Claims Exception Resolution Platform to production.

---

## 1. Database Setup: Amazon DynamoDB

The FastAPI backend stores claim resolutions in a DynamoDB table with the partition key `ClaimID`.

### Option A: Create via AWS CloudShell / CLI (Fastest)
Run the following command in AWS CloudShell:

```bash
aws dynamodb create-table \
    --table-name IntelliSureClaims \
    --attribute-definitions AttributeName=ClaimID,AttributeType=S \
    --key-schema AttributeName=ClaimID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST
```

### Option B: Create via AWS Web Console
1. Open the **DynamoDB Console**.
2. Click **Create table**.
3. Set **Table name** to `IntelliSureClaims`.
4. Set **Partition key** to **`ClaimID`** (String type, case-sensitive).
5. Leave other settings as default and click **Create table**.

Once the table is active, set the environment variable:
```env
CLAIMS_TABLE=IntelliSureClaims
```

---

## 2. Agent Knowledge Base Fixes via AWS CloudShell

If your Bedrock Agent has broken knowledge base associations, you can clean and re-map them using AWS CloudShell.

### Step 1: Disassociate the Old Knowledge Base
Replace `2PGAIW2NCB` with your old/missing ID:
```bash
aws bedrock-agent disassociate-agent-knowledge-base \
    --agent-id PRCWVBE1BE \
    --agent-version "DRAFT" \
    --knowledge-base-id 2PGAIW2NCB
```

### Step 2: Associate the New Knowledge Base
Replace `<NEW_KNOWLEDGE_BASE_ID>` with your new ID:
```bash
aws bedrock-agent associate-agent-knowledge-base \
    --agent-id PRCWVBE1BE \
    --agent-version "DRAFT" \
    --knowledge-base-id <NEW_KNOWLEDGE_BASE_ID> \
    --description "Insurance policy documents repository"
```

### Step 3: Prepare the Agent Draft
```bash
aws bedrock-agent prepare-agent --agent-id PRCWVBE1BE
```

### Step 4: Route the Agent Alias to the Draft Version
```bash
aws bedrock-agent update-agent-alias \
    --agent-id PRCWVBE1BE \
    --agent-alias-id L8XT8TZPEZ \
    --agent-alias-name "production" \
    --routing-configuration '[{"agentVersion": "DRAFT"}]'
```

---

## 3. Deploy Backend to AWS App Runner

AWS App Runner provides a fully managed environment to run the containerized FastAPI backend.

### Step 1: Push Container to Amazon ECR
1. **Create an ECR repository**:
   ```bash
   aws ecr create-repository --repository-name intellisure-backend --region us-east-1
   ```
2. **Login to ECR** (Replace `<YOUR_ACCOUNT_ID>` with your actual AWS Account ID):
   ```bash
   aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <YOUR_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com
   ```
3. **Build the container image** from the project root:
   ```bash
   docker build -t intellisure-backend .
   ```
4. **Tag and Push the container**:
   ```bash
   docker tag intellisure-backend:latest <YOUR_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/intellisure-backend:latest
   docker push <YOUR_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/intellisure-backend:latest
   ```

### Step 2: Create the App Runner Service
1. Open the **AWS App Runner** console and click **Create service**.
2. Source: Select **Container registry** > **Amazon ECR**.
3. Select your ECR image path: `intellisure-backend:latest`.
4. Service configuration:
   * **Port**: Change default to **`8000`** (matching FastAPI container port).
   * **Environment variables**: Add all `.env` keys and values:
     * `AWS_REGION`
     * `BUCKET_NAME`
     * `CLAIMS_TABLE`
     * `CLAIM_AGENT_ID` & `CLAIM_AGENT_ALIAS`
     * `POLICY_AGENT_ID` & `POLICY_AGENT_ALIAS`
     * `ADJUDICATION_AGENT_ID` & `ADJUDICATION_AGENT_ALIAS`
     * `AUDIT_AGENT_ID` & `AUDIT_AGENT_ALIAS`
5. Security IAM Instance Role:
   Ensure the role attached to App Runner has permissions to read/write to your resources:
   * `AmazonS3FullAccess`
   * `AmazonDynamoDBFullAccess`
   * `AmazonBedrockFullAccess`
6. Click **Create & Deploy**. App Runner will generate a public URL (e.g. `https://xxxxxx.us-east-1.awsapprunner.com`).

---

## 4. Deploy Frontend to AWS Amplify

AWS Amplify Hosting is optimized for deploying static web applications such as React Vite.

### Step 1: Connect your Git Repository
1. Push this project repository to GitHub, GitLab, or Bitbucket.
2. Open the **AWS Amplify** console and click **New App** > **Host web app**.
3. Choose your Git provider, authorize it, and select your repository and branch.

### Step 2: Configure Subdirectory Build Settings
Because the React code lives in the `frontend` subdirectory, update the **Build settings YAML** to match:

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

### Step 3: Add the API Environment Variable
1. In the deployment configuration page, click on **Advanced settings**.
2. Add the API URL environment variable:
   * **Key**: `VITE_API_URL`
   * **Value**: Your App Runner public URL (e.g., `https://xxxxxx.us-east-1.awsapprunner.com`)

### Step 4: Deploy
Click **Save and Deploy**. AWS Amplify will build, deploy, and host your dashboard on a secure `https://xxx.amplifyapp.com` domain.
