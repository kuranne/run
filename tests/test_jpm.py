import pytest
from pathlib import Path
from runner.jpm import JPM

def test_get_main_class(tmp_path):
    java_file = tmp_path / "Main.java"
    java_file.write_text("public class Main {}")
    
    assert JPM.get_main_class(java_file) == "Main"

def test_record_and_get_new_class_files(tmp_path):
    # Setup some dummy class files
    class1 = tmp_path / "First.class"
    class1.write_text("dummy")
    
    directories = {tmp_path}
    
    # Record state
    state = JPM.record_class_files(directories)
    assert class1 in state
    
    # Create new class file
    class2 = tmp_path / "Second.class"
    class2.write_text("dummy2")
    
    # Get new class files
    new_files = JPM.get_new_class_files(directories, state)
    
    assert class1 not in new_files
    assert class2 in new_files

def test_get_main_file_java(tmp_path):
    helper = tmp_path / "Helper.java"
    helper.write_text("public class Helper { public void run() {} }")

    app = tmp_path / "App.java"
    app.write_text("""
    public class App {
        public static void main(String[] args) {
            System.out.println("Hello");
        }
    }
    """)

    assert JPM.get_main_file([helper, app]) == app
    assert JPM.get_main_file([app, helper]) == app

def test_get_main_file_varargs_and_comments(tmp_path):
    app = tmp_path / "App.java"
    app.write_text("""
    /* public static void main(String[] args) {} */
    // public static void main(String[] args) {}
    public class App {
        public static void main(String... args) {
        }
    }
    """)
    assert JPM.get_main_file([app]) == app

def test_get_main_file_none(tmp_path):
    helper = tmp_path / "Helper.java"
    helper.write_text("public class Helper {}")
    assert JPM.get_main_file([helper]) is None
