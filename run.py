#!/usr/bin/env python3
"""
Quick startup script for Smart Telegram Bot
Provides additional checks and user-friendly error messages
"""

import sys
import os
import subprocess
from pathlib import Path

def check_python_version():
    """Check if Python version is compatible"""
    if sys.version_info < (3, 8):
        print("❌ Python 3.8+ required. Current version:", sys.version)
        return False
    print(f"✅ Python {sys.version.split()[0]} - OK")
    return True

def check_dependencies():
    """Check if all required dependencies are installed"""
    required_packages = [
        'telegram',
        'aiohttp', 
        'sentence_transformers',
        'numpy',
        'dotenv',
        'pytz'
    ]
    
    missing = []
    for package in required_packages:
        try:
            __import__(package.replace('-', '_'))
            print(f"✅ {package} - OK")
        except ImportError:
            missing.append(package)
            print(f"❌ {package} - Missing")
    
    if missing:
        print(f"\n📦 Install missing packages:")
        print(f"pip install {' '.join(missing)}")
        print("Or install all dependencies:")
        print("pip install -r requirements.txt")
        return False
    
    return True

def check_env_file():
    """Check if .env file exists and has required variables"""
    env_path = Path('.env')
    if not env_path.exists():
        print("❌ .env file not found")
        print("📝 Create .env file from template:")
        print("cp .env.example .env")
        print("Then edit .env with your bot token and settings")
        return False
    
    print("✅ .env file found")
    
    # Check required variables
    required_vars = ['TELEGRAM_BOT_TOKEN', 'CHAT_ID']
    missing_vars = []
    
    try:
        with open('.env', 'r') as f:
            content = f.read()
            for var in required_vars:
                if f"{var}=" not in content or f"{var}=your_" in content:
                    missing_vars.append(var)
        
        if missing_vars:
            print(f"⚠️  Please configure these variables in .env:")
            for var in missing_vars:
                print(f"   - {var}")
            return False
        
        print("✅ Required environment variables configured")
        return True
        
    except Exception as e:
        print(f"❌ Error reading .env file: {e}")
        return False

def check_lm_studio():
    """Check if LM Studio is accessible"""
    try:
        import aiohttp
        import asyncio
        
        async def test_connection():
            try:
                timeout = aiohttp.ClientTimeout(total=5)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get('http://localhost:1234/v1/models') as response:
                        if response.status == 200:
                            return True
            except:
                pass
            return False
        
        is_accessible = asyncio.run(test_connection())
        if is_accessible:
            print("✅ LM Studio is accessible")
            return True
        else:
            print("⚠️  LM Studio not accessible at http://localhost:1234")
            print("   Make sure LM Studio is running with a loaded model")
            print("   Bot will still start but AI responses won't work")
            return True  # Don't block startup
            
    except Exception as e:
        print(f"⚠️  Could not check LM Studio: {e}")
        return True  # Don't block startup

def main():
    """Main startup function"""
    print("🚀 Smart Telegram Bot - Startup Check")
    print("=" * 40)
    
    # Run all checks
    checks = [
        ("Python Version", check_python_version),
        ("Dependencies", check_dependencies), 
        ("Configuration", check_env_file),
        ("LM Studio", check_lm_studio)
    ]
    
    all_passed = True
    for name, check_func in checks:
        print(f"\n🔍 Checking {name}...")
        if not check_func():
            all_passed = False
    
    print("\n" + "=" * 40)
    
    if all_passed:
        print("✅ All checks passed! Starting bot...")
        print("=" * 40)
        
        # Import and run the bot
        try:
            from bot import main as bot_main
            import asyncio
            asyncio.run(bot_main())
        except KeyboardInterrupt:
            print("\n👋 Bot stopped by user")
        except Exception as e:
            print(f"\n❌ Error starting bot: {e}")
            sys.exit(1)
    else:
        print("❌ Some checks failed. Please fix the issues above.")
        sys.exit(1)

if __name__ == "__main__":
    main()
