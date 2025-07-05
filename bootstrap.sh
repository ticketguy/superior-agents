
set -e

# VaultGuard Bootstrap Script - Web3 Security Agent Setup

echo "🛡️ VaultGuard - Web3 Security Agent Framework"
echo "============================================="

check_python_version() {
	required="3.12"
	current=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
	if [[ $(echo "$current < $required" | bc -l 2>/dev/null || echo "1") -eq 1 ]]; then
		echo "❌ Error: Python $required+ required (found $current)" >&2
		echo "Please install Python 3.12+ and try again"
		exit 1
	fi
	echo "✅ Python $current detected"
}

check_dependencies() {
	echo "🔍 Checking system dependencies..."
	
	# Check Python
	check_python_version
	
	# Check Docker
	if command -v docker >/dev/null 2>&1; then
		echo "✅ Docker detected"
	else
		echo "⚠️ Docker not found - required for containerized deployment"
	fi
	
	# Check Docker Compose
	if command -v docker compose >/dev/null 2>&1; then
		echo "✅ Docker Compose detected"
	else
		echo "⚠️ Docker Compose not found - required for containerized deployment"
	fi
	
	# Check bc for version comparison
	if ! command -v bc >/dev/null 2>&1; then
		echo "⚠️ Installing bc for version checks..."
		sudo apt-get update && sudo apt-get install -y bc 2>/dev/null || echo "Please install 'bc' manually"
	fi
}

setup_agent() {
	echo "🐍 Setting up Security Agent environment..."
	
	# Create virtual environment
	python3 -m venv agent-venv
	source agent-venv/bin/activate
	
	# Install agent dependencies
	cd agent
	echo "📦 Installing agent dependencies..."
	pip install -e . >/dev/null 2>&1
	
	# Setup environment file
	if [ ! -f .env ]; then
		cp .env.example .env
		echo "📝 Created agent/.env from example"
	else
		echo "📝 agent/.env already exists"
	fi
	
	cd ..
	deactivate
	echo "✅ Security Agent environment ready"
}

setup_rag() {
	echo "🧠 Setting up RAG API environment..."
	
	cd rag-api
	
	# Install RAG dependencies
	echo "📦 Installing RAG dependencies..."
	pip install -r requirements.txt >/dev/null 2>&1
	
	# Setup environment file
	if [ ! -f .env ]; then
		cp .env.example .env 2>/dev/null || echo "OPENAI_API_KEY=" > .env
		echo "📝 Created rag-api/.env"
	else
		echo "📝 rag-api/.env already exists"
	fi
	
	# Create necessary directories
	mkdir -p pkl db
	
	cd ..
	echo "✅ RAG API environment ready"
}

setup_directories() {
	echo "📁 Setting up project directories..."
	
	# Create necessary directories
	mkdir -p db agent/code agent/db rag-api/pkl
	
	# Set permissions
	chmod 755 db agent/code agent/db rag-api/pkl
	
	echo "✅ Project directories created"
}

display_next_steps() {
	echo ""
	echo "🎉 VaultGuard setup complete!"
	echo "=============================="
	echo ""
	echo "📝 Next steps:"
	echo ""
	echo "1. Configure API Keys:"
	echo "   📝 Edit agent/.env with your API keys:"
	echo "      - ANTHROPIC_API_KEY (for Claude)"
	echo "      - OPENAI_API_KEY (for OpenAI/embeddings)"
	echo "      - SOLANA_RPC_URL (Solana blockchain access)"
	echo ""
	echo "   📝 Edit rag-api/.env with:"
	echo "      - OPENAI_API_KEY (for vector embeddings)"
	echo ""
	echo "2. Start VaultGuard:"
	echo ""
	echo "   🚀 Method 1 - Individual Services:"
	echo "      Terminal 1: cd rag-api && python scripts/api.py"
	echo "      Terminal 2: cd agent && python scripts/starter.py"
	echo ""
	echo "   🐳 Method 2 - Docker Compose:"
	echo "      docker compose up --build"
	echo ""
	echo "3. Access VaultGuard:"
	echo "   🌐 RAG API: http://localhost:8080"
	echo "   🛡️ Security Agent: http://localhost:8001"
	echo ""
	echo "📚 For more information, see README.md"
	echo ""
	echo "⚠️ Security Notice:"
	echo "   Never share your private keys or API keys"
	echo "   VaultGuard analyzes transactions but never accesses private keys"
}

main() {
	echo "🚀 Starting VaultGuard setup..."
	echo ""
	
	# Check system dependencies
	check_dependencies
	echo ""
	
	# Setup project directories
	setup_directories
	echo ""
	
	# Setup agent environment
	setup_agent
	echo ""
	
	# Setup RAG environment  
	setup_rag
	echo ""
	
	# Display next steps
	display_next_steps
}

# Run main function
main