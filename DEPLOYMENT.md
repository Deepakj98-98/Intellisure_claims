# IntelliSure AI: Deployment & Infrastructure Configuration Guide

This guide provides step-by-step instructions for setting up the required AWS infrastructure and deploying the IntelliSure AI Claims Exception Resolution Platform to production.

---

## 1. Database Setup: Amazon DynamoDB

The FastAPI backend stores ONE record PER PIPELINE STAGE per claim
(Intake, Policy Validation, Adjudication, Cross-Lens Reconciliation,
Execution/Notification, Audit) — not a single combined record — so
the table needs a partition key AND a sort key. See
`backend/README.md`'s "Resilience & Audit Trail" section for why this
matters (a claim that fails partway through still has every completed
stage permanently recorded).

### Option A: Create via AWS CloudShell / CLI (Fastest)
Run the following command in AWS CloudShell:

```bash
aws dynamodb create-table \
    --table-name IntelliSureClaims \
    --attribute-definitions \
        AttributeName=ClaimID,AttributeType=S \
        AttributeName=StageTimestamp,AttributeType=S \
    --key-schema \
        AttributeName=ClaimID,KeyType=HASH \
        AttributeName=StageTimestamp,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST
```

### Option B: Create via AWS Web Console
1. Open the **DynamoDB Console**.
2. Click **Create table**.
3. Set **Table name** to `IntelliSureClaims`.
4. Set **Partition key** to **`ClaimID`** (String type, case-sensitive).
5. Set **Sort key** to **`StageTimestamp`** (String type) — **do not
   skip this**, it's what allows multiple stage records per claim
   instead of overwriting a single record on every stage write.
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

## 3. Deploy Backend — ECR + CodeBuild + ECS Express Mode

This is the intended deployment path for this project: **AWS CodeBuild**
builds and pushes the container image (using `buildspec.yml` and
`Dockerfile`, already in this repo), and **Amazon ECS Express Mode**
runs it. Express Mode is the simplified ECS/Fargate deployment path —
it provisions the load balancer, networking, and auto-scaling for you
through a guided console flow, without hand-configuring a full ECS
cluster/service/task-definition setup yourself. (App Runner steps are
also included further below as a documented alternative, in case
Express Mode's console flow has changed since this was written or
isn't available in your account/region — same container image, either
target works.)

### Step 1: Set up CodeBuild to build and push to ECR

1. **Create the ECR repository** (matches `buildspec.yml`'s
   `IMAGE_REPO_NAME: claims_app` — keep these in sync, or update
   `buildspec.yml` if you name it differently):
   ```bash
   aws ecr create-repository --repository-name claims_app --region us-east-1
   ```
2. **Create a CodeBuild project** pointing at your GitHub repo:
   - Console → CodeBuild → **Create build project**
   - Source: **GitHub**, connect your repo, select your branch
   - Environment: **Managed image**, Ubuntu, standard runtime, and
     check **"Privileged"** (required — this project builds a Docker
     image, which needs privileged mode to run the Docker daemon
     inside the build container)
   - Buildspec: **Use a buildspec file** — it will automatically find
     `buildspec.yml` at the repo root
   - Service role: let CodeBuild create a new one, then add ECR push
     permissions (`AmazonEC2ContainerRegistryPowerUser` is the
     simplest managed policy for this)
3. **Run the build** — Console → your CodeBuild project → **Start
   build**. Watch the logs; on success, your image is now in ECR,
   tagged `latest`.
4. **(Optional) Automate this on every push** — CodeBuild → your
   project → Edit → Source → add a **webhook** triggering on push to
   your branch, so every commit automatically rebuilds and re-pushes
   the image without you running this manually each time.

### Step 2: Deploy to ECS Express Mode

1. Console → **Elastic Container Service** → look for the **Express
   Mode** / "Create an application" guided flow (this is a newer,
   simplified ECS onboarding path — if your console shows a different
   label than expected, look for "Deploy a container application" or
   similar guided wizard; the underlying steps below are the same
   regardless of exact wording)
2. **Container image**: select your ECR repository (`claims_app:latest`)
3. **Port**: `8000` (matches `Dockerfile`'s `EXPOSE 8000` and the
   `uvicorn` command's port)
4. **Environment variables**: add every key from `backend/README.md`'s
   Environment Variables section — `AWS_REGION`, `BUCKET_NAME`,
   `CLAIMS_TABLE`, all four agent ID/alias pairs, and the newer
   optional ones (`CROSS_LENS_AGENT_ID`, `SES_ENABLED`, etc.) if you're
   using them
5. **IAM permissions**: the task role needs read/write access to your
   S3 bucket, DynamoDB table, and `bedrock:InvokeAgent` /
   `bedrock:Retrieve` — scope a custom policy to your specific
   resources rather than attaching `AmazonS3FullAccess` /
   `AmazonDynamoDBFullAccess` / `AmazonBedrockFullAccess` if you have
   time; the broad managed policies work but are not least-privilege
   (worth naming as a known shortcut if a judge asks about IAM scoping)
6. Deploy — Express Mode provisions the load balancer and gives you a
   public URL once healthy. Test it: `curl https://<your-url>/health`

### Alternative: AWS App Runner (same container image)

If Express Mode isn't available or you hit friction with it:

1. Console → **App Runner** → **Create service**
2. Source: **Container registry** → **Amazon ECR** → select
   `claims_app:latest`
3. Port: `8000`
4. Add the same environment variables as Step 2 above
5. Same IAM scoping note applies to App Runner's instance role
6. **Create & Deploy** — App Runner generates a public URL
   (`https://xxxxxx.us-east-1.awsapprunner.com`)

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
