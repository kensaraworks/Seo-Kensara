# KensaraAI Autonomous SEO Pipeline

An intelligent, fully automated web application and background service designed to scan global privacy feeds, identify high-priority compliance events (e.g., DPDPA signals), and autonomously draft and publish SEO-optimized blogs.

## 🚀 Features
- **Live Intelligence Feed**: Analyzes 15+ global privacy feeds using Tavily and Serper APIs to surface actionable events.
- **Autonomous Blog Generation**: Multi-agent orchestration to generate high-quality compliance blogs using NVIDIA NIMs (Mistral, Qwen, DeepSeek) and Groq (Llama 3.3).
- **Resilient SSE Workflows**: Built with Server-Sent Events (SSE) and robust Keep-Alive mechanisms to ensure the UI stays connected even during 90-second+ heavy LLM reasoning phases.
- **Automated CI/CD**: Seamless deployment to Azure App Service via GitHub Actions upon every merge to `master`.

## 🛠️ Architecture
- **Backend**: FastAPI (Python) running under Uvicorn.
- **Frontend**: Lightweight HTML/CSS template engine integrated with Jinja2 for dynamic dashboard rendering.
- **AI Models**: NVIDIA NIM APIs, Groq, with fallback reasoning logic.
- **Infrastructure**: Azure App Service (Linux) with auto-injected hardware-level secrets.

## ⚙️ Local Setup
To run this project locally on your machine:

1. **Clone the repository:**
   ```bash
   git clone https://github.com/KensaraAI/AI-ML_contributions_KensaraAI.git
   cd AI-ML_contributions_KensaraAI
   ```

2. **Create a virtual environment and install dependencies:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables:**
   Create a `settings.json` file in the root directory (this file is gitignored for security) with the following structure:
   ```json
   {
     "CONTENT_OUTPUT_DIR": "./drafts",
     "WEBSITES_PORT": "8000",
     "NVIDIA_API_KEY": "your_nvidia_api_key",
     "GROQ_API_KEY": "your_groq_api_key",
     "TAVILY_API_KEY": "your_tavily_api_key",
     "SERPER_API_KEY": "your_serper_api_key"
   }
   ```

4. **Run the Application:**
   ```bash
   python -m uvicorn src.ui.app:app --host 0.0.0.0 --port 8000 --reload
   ```
   Access the dashboard locally at `http://localhost:8000`.

## ☁️ Deployment
This repository is configured with a GitHub Actions workflow `.github/workflows/deploy.yml`. 
When code is pushed to the `master` branch, GitHub will automatically build the container and deploy it directly to the connected Azure App Service instance (`kensarai-seo-app-8321`). All API keys and environment variables are managed securely inside the Azure Configuration dashboard.
