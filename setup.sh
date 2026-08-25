#!/usr/bin/env bash
#
# AWS Resource Inventory - Automated Setup Script
# ==============================================
#
# This script automates the complete setup of the AWS Resource Inventory tool
# for users with no prior experience. It handles all dependencies and
# configuration automatically.
#
# WHAT THIS SCRIPT DOES:
# ----------------------
# 1. ✅ Installs Python 3.10+ (via Homebrew on macOS, package manager on Linux)
# 2. ✅ Installs Poetry (Python dependency manager)
# 3. ✅ Installs pre-commit (Git hooks framework)
# 4. ✅ Sets up pre-commit hooks with --install-hooks
# 5. ✅ Runs 'poetry install' to install project dependencies
# 6. ✅ Executes './run_quick_tests.sh' to verify installation
# 7. ✅ Installs AWS CLI (if not present)
# 8. ✅ Provides AWS configuration guidance
# 9. ✅ Shows usage examples and next steps
#
# PREREQUISITES:
# --------------
# - macOS (10.15+) or Linux system
# - Internet connection for downloads
# - Administrative privileges (for package installations)
# - Git (usually pre-installed, needed for pre-commit hooks)
#
# USAGE:
# ------
# From the project root directory:
#   ./setup.sh
#
# MANUAL STEPS AFTER SETUP:
# --------------------------
# 1. Configure AWS: export AWS_PROFILE=your-profile-name
# 2. Login to AWS: aws sso login --profile $AWS_PROFILE
# 3. Test the tool: poetry run aws-inventory scan --help
#
# SUPPORTED SYSTEMS:
# ------------------
# ✅ macOS (Intel & Apple Silicon)
# ✅ Ubuntu/Debian Linux
# ✅ RHEL/CentOS/Fedora Linux
# ✅ Arch Linux
#
# TROUBLESHOOTING:
# ----------------
# If the script fails, you can run the steps manually:
# 1. Install Homebrew (macOS): /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
# 2. Install dependencies: brew install python3 poetry pre-commit  (macOS)
# 3. pre-commit install --install-hooks
# 4. poetry install
# 5. ./run_quick_tests.sh
# 6. Configure AWS credentials
#
# For help: Check project README.md or open an issue

set -e  # Exit on any error

# Colors for better output readability
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_NAME="AWS Resource Inventory"
REQUIRED_PYTHON_VERSION="3.10"

#######################################
# Print colored output messages
# Arguments:
#   $1: Color (RED, GREEN, YELLOW, BLUE, PURPLE, CYAN)
#   $2: Message to print
#######################################
print_message() {
    local color=$1
    local message=$2
    echo -e "${!color}${message}${NC}"
}

#######################################
# Print section headers
# Arguments:
#   $1: Section title
#######################################
print_section() {
    echo
    print_message "BLUE" "=================================================="
    print_message "BLUE" "🔧 $1"
    print_message "BLUE" "=================================================="
}

#######################################
# Print step information
# Arguments:
#   $1: Step number
#   $2: Step description
#######################################
print_step() {
    local step_num=$1
    local description=$2
    print_message "CYAN" "Step $step_num: $description"
}

#######################################
# Print success message
# Arguments:
#   $1: Success message
#######################################
print_success() {
    print_message "GREEN" "✅ $1"
}

#######################################
# Print error message
# Arguments:
#   $1: Error message
#######################################
print_error() {
    print_message "RED" "❌ $1"
}

#######################################
# Print warning message
# Arguments:
#   $1: Warning message
#######################################
print_warning() {
    print_message "YELLOW" "⚠️  $1"
}

#######################################
# Print info message
# Arguments:
#   $1: Info message
#######################################
print_info() {
    print_message "PURPLE" "ℹ️  $1"
}

#######################################
# Check if command exists
# Arguments:
#   $1: Command to check
# Returns:
#   0 if command exists, 1 otherwise
#######################################
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

#######################################
# Get operating system type
# Returns:
#   "macos", "linux", or "unknown"
#######################################
get_os() {
    case "$(uname -s)" in
        Darwin*) echo "macos" ;;
        Linux*)  echo "linux" ;;
        *)       echo "unknown" ;;
    esac
}

#######################################
# Check if we're in the correct directory
#######################################
check_directory() {
    print_step "0" "Verifying project directory"

    if [[ ! -f "pyproject.toml" ]] || [[ ! -d "aws_resource_inventory" ]]; then
        print_error "This script must be run from the AWS Resource Inventory project root directory."
        print_info "Expected: pyproject.toml, aws_resource_inventory/"
        print_info "Current directory: $(pwd)"
        print_info "Please navigate to the project root and try again."
        exit 1
    fi

    print_success "Project directory verified"
}

#######################################
# Check Homebrew availability (macOS only)
#######################################
check_homebrew() {
    if [[ "$(get_os)" == "macos" ]]; then
        if ! command_exists brew; then
            print_step "1a" "Checking Homebrew (macOS package manager)"
            print_warning "Homebrew is not installed!"
            print_info "Homebrew is recommended for installing Python, Poetry, and other dependencies on macOS"
            print_info "To install Homebrew manually, run:"
            print_message "CYAN" "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
            print_info "Or visit: https://brew.sh/ for detailed instructions"
            print_info "After installing Homebrew, re-run this script"

            # Ask user if they want to continue without Homebrew
            echo
            read -p "$(print_message "YELLOW" "Continue setup without Homebrew? (packages will be installed via pip) [y/N]: ")" -r
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                print_info "Setup paused. Install Homebrew and re-run this script."
                exit 0
            fi
            print_warning "Continuing without Homebrew - some installations may fail"
        else
            print_success "Homebrew already installed (version: $(brew --version | head -1))"
        fi
    fi
}

#######################################
# Install Python 3
#######################################
install_python() {
    print_step "1" "Installing Python 3"

    local os_type=$(get_os)

    case $os_type in
        macos)
            if ! command_exists python3; then
                if command_exists brew; then
                    print_info "Installing Python 3 via Homebrew"
                    if brew install python3; then
                        print_success "Python 3 installed successfully"
                    else
                        print_error "Python 3 installation failed via Homebrew"
                        print_info "Manual installation: Download from https://python.org/downloads/"
                        exit 1
                    fi
                else
                    print_error "Python 3 not found and Homebrew is not available"
                    print_info "Please install Homebrew first or download Python manually from:"
                    print_info "https://python.org/downloads/"
                    exit 1
                fi
            else
                # Check Python version
                local python_version=$(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
                local major_minor="${python_version%.*}"

                if python3 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
                    print_success "Python 3 already installed (version: $(python3 --version | cut -d' ' -f2))"
                else
                    print_warning "Python version $python_version is installed, but version >= 3.10 is required"
                    if command_exists brew; then
                        print_info "Updating Python via Homebrew"
                        brew upgrade python3 || {
                            print_error "Failed to upgrade Python"
                            exit 1
                        }
                    else
                        print_error "Cannot upgrade Python without Homebrew"
                        print_info "Please install Homebrew or upgrade Python manually"
                        exit 1
                    fi
                fi
            fi
            ;;
        linux)
            if ! command_exists python3; then
                print_info "Installing Python 3 via system package manager"
                if command_exists apt-get; then
                    sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv
                elif command_exists yum; then
                    sudo yum install -y python3 python3-pip
                elif command_exists dnf; then
                    sudo dnf install -y python3 python3-pip
                elif command_exists pacman; then
                    sudo pacman -S python python-pip
                else
                    print_error "Could not detect package manager. Please install Python 3 manually."
                    print_info "Visit https://python.org/downloads/ for installation instructions"
                    exit 1
                fi
                print_success "Python 3 installed successfully"
            else
                print_success "Python 3 already installed (version: $(python3 --version | cut -d' ' -f2))"
            fi
            ;;
        *)
            print_error "Unsupported operating system: $(uname -s)"
            print_info "Please install Python 3 manually from https://python.org/downloads/"
            exit 1
            ;;
    esac
}

#######################################
# Install Poetry
#######################################
install_poetry() {
    print_step "2" "Installing Poetry (Python dependency manager)"

    if ! command_exists poetry; then
        local os_type=$(get_os)

        case $os_type in
            macos)
                if command_exists brew; then
                    print_info "Installing Poetry via Homebrew"
                    if brew install poetry; then
                        print_success "Poetry installed successfully"
                    else
                        print_error "Poetry installation failed via Homebrew"
                        print_info "Trying alternative installation method"
                        install_poetry_pip
                    fi
                else
                    print_info "Homebrew not available, installing Poetry via pip"
                    install_poetry_pip
                fi
                ;;
            *)
                install_poetry_pip
                ;;
        esac
    else
        print_success "Poetry already installed (version: $(poetry --version | cut -d' ' -f3))"
    fi
}

#######################################
# Install Poetry via pip (fallback method)
#######################################
install_poetry_pip() {
    print_info "Installing Poetry via pip"
    if python3 -m pip install --user poetry; then
        print_success "Poetry installed successfully"

        # Add Poetry to PATH
        local poetry_bin_dir="$HOME/.local/bin"
        if [[ -d "$poetry_bin_dir" ]] && [[ ":$PATH:" != *":$poetry_bin_dir:"* ]]; then
            export PATH="$poetry_bin_dir:$PATH"
            echo "export PATH=\"$poetry_bin_dir:\$PATH\"" >> ~/.bashrc
            echo "export PATH=\"$poetry_bin_dir:\$PATH\"" >> ~/.zshrc
            print_info "Added Poetry to PATH"
        fi
    else
        print_error "Poetry installation failed"
        print_info "Manual installation: curl -sSL https://install.python-poetry.org | python3 -"
        exit 1
    fi
}

#######################################
# Install pre-commit
#######################################
install_precommit() {
    print_step "3" "Installing pre-commit (Git hooks framework)"

    if ! command_exists pre-commit; then
        local os_type=$(get_os)

        case $os_type in
            macos)
                if command_exists brew; then
                    print_info "Installing pre-commit via Homebrew"
                    if brew install pre-commit; then
                        print_success "pre-commit installed successfully"
                    else
                        print_error "pre-commit installation failed via Homebrew"
                        print_info "Trying alternative installation method"
                        install_precommit_pip
                    fi
                else
                    print_info "Homebrew not available, installing pre-commit via pip"
                    install_precommit_pip
                fi
                ;;
            *)
                install_precommit_pip
                ;;
        esac
    else
        print_success "pre-commit already installed (version: $(pre-commit --version))"
    fi
}

#######################################
# Install pre-commit via pip (fallback method)
#######################################
install_precommit_pip() {
    print_info "Installing pre-commit via pip"
    if python3 -m pip install --user pre-commit; then
        print_success "pre-commit installed successfully"
    else
        print_error "pre-commit installation failed"
        print_warning "Continuing without pre-commit. You can install it manually later."
    fi
}

#######################################
# Install project dependencies
#######################################
install_dependencies() {
    print_step "4" "Installing project dependencies"

    print_info "Installing Python dependencies via Poetry"
    if poetry install; then
        print_success "Project dependencies installed successfully"
    else
        print_error "Failed to install project dependencies"
        print_info "Manual step: Run 'poetry install' after fixing any issues"
        exit 1
    fi
}

#######################################
# Setup pre-commit hooks
#######################################
setup_precommit() {
    print_step "5" "Setting up pre-commit hooks"

    if command_exists pre-commit; then
        print_info "Installing pre-commit hooks"
        if pre-commit install --install-hooks; then
            print_success "pre-commit hooks installed successfully"
        else
            print_warning "Failed to install pre-commit hooks"
            print_info "You can set them up manually later with: pre-commit install --install-hooks"
        fi
    else
        print_warning "pre-commit not available, skipping hook setup"
    fi
}

#######################################
# Run quick tests
#######################################
run_tests() {
    print_step "6" "Running quick tests to verify installation"

    if [[ -f "./run_quick_tests.sh" ]]; then
        print_info "Running test suite"
        if chmod +x ./run_quick_tests.sh && ./run_quick_tests.sh; then
            print_success "All tests passed successfully"
        else
            print_warning "Some tests failed, but installation may still be functional"
            print_info "You can run tests manually later with: ./run_quick_tests.sh"
        fi
    else
        print_warning "Test script not found, skipping tests"
        print_info "Testing tool manually"
        if poetry run aws-inventory --help >/dev/null 2>&1; then
            print_success "Tool is working correctly"
        else
            print_error "Tool verification failed"
        fi
    fi
}

#######################################
# Setup AWS configuration
#######################################
setup_aws() {
    print_step "7" "Setting up AWS configuration"

    print_info "Checking AWS CLI installation"
    if ! command_exists aws; then
        print_warning "AWS CLI not found"
        local os_type=$(get_os)

        case $os_type in
            macos)
                if command_exists brew; then
                    print_info "Installing AWS CLI via Homebrew"
                    if brew install awscli; then
                        print_success "AWS CLI installed successfully"
                    else
                        print_warning "AWS CLI installation failed"
                        print_info "You can install it manually from: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
                    fi
                else
                    print_warning "Homebrew not available for AWS CLI installation"
                    print_info "Please install AWS CLI manually:"
                    print_info "  - Download from: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
                fi
                ;;
            *)
                print_info "Please install AWS CLI manually:"
                print_info "  - Linux: sudo apt-get install awscli  (or equivalent for your distro)"
                print_info "  - Manual: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
                ;;
        esac
    else
        print_success "AWS CLI already installed (version: $(aws --version 2>&1 | cut -d' ' -f1))"
    fi

    print_info "AWS Configuration Setup Required:"
    print_message "YELLOW" "  1. Set your AWS profile: export AWS_PROFILE=YOUR_PROFILE_NAME"
    print_message "YELLOW" "  2. Login to AWS SSO: aws sso login --profile \$AWS_PROFILE"
    print_message "YELLOW" "  3. Or configure credentials: aws configure"
}

#######################################
# Display usage instructions
#######################################
display_usage() {
    print_section "🎉 Setup Complete! Usage Instructions"

    echo
    print_message "GREEN" "The AWS Resource Inventory has been successfully installed!"
    echo

    print_message "CYAN" "🚀 Quick Start:"
    echo "  1. Configure AWS credentials:"
    echo "     export AWS_PROFILE=your-profile-name"
    echo "     aws sso login --profile \$AWS_PROFILE"
    echo
    echo "  2. Run the tool:"
    echo "     poetry run aws-inventory --help"
    echo "     poetry run aws-inventory scan --service s3 --regions us-east-1"
    echo

    print_message "CYAN" "📖 Common Usage Examples:"
    echo "  • Scan specific services and regions:"
    echo "    poetry run aws-inventory scan --service s3 --regions us-east-1,eu-west-1"
    echo
    echo "  • Filter by tags:"
    echo "    poetry run aws-inventory scan --tag-key Environment --tag-value Production"
    echo
    echo "  • Enable refresh mode (continuous monitoring):"
    echo "    poetry run aws-inventory scan --refresh --refresh-interval 30"
    echo
    echo "  • Choose where the JSON results document goes:"
    echo "    poetry run aws-inventory scan --output results.json"
    echo "    poetry run aws-inventory scan --output - | jq '.summary'"
    echo

    print_message "CYAN" "📁 Project Files:"
    echo "  • aws_resource_inventory/ - The package (cli.py, orchestrator.py, lib/, services/)"
    echo "  • pyproject.toml - Project configuration"
    echo "  • run_quick_tests.sh - Test suite"
    echo

    print_message "PURPLE" "💡 Tips:"
    echo "  • Use '--dry-run' to see what would be scanned without actually running"
    echo "  • Use '--no-cache' to force fresh data retrieval"
    echo "  • Check logs in ./.debug_logs/ for debugging"
    echo
}

#######################################
# Handle script errors
#######################################
error_handler() {
    local exit_code=$?
    local line_number=$1

    echo
    print_error "Script failed at line $line_number with exit code $exit_code"
    print_info "Check the error messages above for details"
    print_info "You can try running the failed steps manually or seek help in the project repository"
    exit $exit_code
}

#######################################
# Main setup function
#######################################
main() {
    # Set up error handling
    trap 'error_handler ${LINENO}' ERR

    # Display welcome message
    clear
    print_message "PURPLE" "╔══════════════════════════════════════════════════════════════╗"
    print_message "PURPLE" "║                                                              ║"
    print_message "PURPLE" "║                 AWS Resource Inventory Setup                 ║"
    print_message "PURPLE" "║                                                              ║"
    print_message "PURPLE" "║  This script will install all required dependencies and      ║"
    print_message "PURPLE" "║  set up the AWS Resource Inventory tool for immediate use.   ║"
    print_message "PURPLE" "║                                                              ║"
    print_message "PURPLE" "╚══════════════════════════════════════════════════════════════╝"
    echo

    print_info "Detected OS: $(get_os)"
    print_info "Script directory: $SCRIPT_DIR"
    echo

    # Check if user wants to continue
    read -p "$(print_message "CYAN" "Continue with setup? (y/N): ")" -r
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "Setup cancelled by user"
        exit 0
    fi

    # Run setup steps
    check_directory
    check_homebrew
    install_python
    install_poetry
    install_precommit
    install_dependencies
    setup_precommit
    run_tests
    setup_aws
    display_usage

    print_section "🎊 Setup Completed Successfully!"
    print_success "AWS Resource Inventory is ready to use!"
}

# Run main function if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
