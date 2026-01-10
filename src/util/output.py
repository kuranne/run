class Colors:
    GREEN = '\033[92m'
    CYAN = '\033[96m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    GRAY = '\033[1;30m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

class Printer:
    @staticmethod
    def action(tag: str, message: str, color: str = Colors.GREEN):
        # [ TAG ] Message
        print(f"{Colors.BOLD}{color}[ {tag} ]{Colors.RESET} {message}")

    @staticmethod
    def time(seconds: float):
         print(f"{Colors.GRAY}  -> Took {seconds:.3f}s{Colors.RESET}")

    @staticmethod
    def error(message: str):
        print(f"{Colors.BOLD}{Colors.RED}[ ERROR ]{Colors.RESET} {message}")
    
    @staticmethod
    def info(message: str):
         print(f"{Colors.BOLD}{Colors.CYAN}[ INFO ]{Colors.RESET} {message}")

    @staticmethod
    def warning(message: str):
         print(f"{Colors.BOLD}{Colors.YELLOW}[ WARN ]{Colors.RESET} {message}")

    @staticmethod
    def separator():
        print(f"\n{Colors.GRAY}{'-'*30}{Colors.RESET}\n")
