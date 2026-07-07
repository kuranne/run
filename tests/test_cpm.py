import pytest
from pathlib import Path
from runner.cpm import CPM

def test_get_main_file_c(tmp_path):
    main_file = tmp_path / "main.c"
    main_file.write_text("int main(int argc, char** argv) { return 0; }")
    
    util_file = tmp_path / "util.c"
    util_file.write_text("void do_something() {}")
    
    assert CPM.get_main_file([util_file, main_file]) == main_file

def test_get_main_file_cpp(tmp_path):
    main_file = tmp_path / "app.cpp"
    main_file.write_text("int main() { return 0; }")
    
    assert CPM.get_main_file([main_file]) == main_file

def test_get_main_file_wmain(tmp_path):
    main_file = tmp_path / "win.cpp"
    main_file.write_text("int wmain() { return 0; }")
    
    assert CPM.get_main_file([main_file]) == main_file

def test_get_main_file_with_comments(tmp_path):
    main_file = tmp_path / "main.c"
    main_file.write_text("""
    /* int main() { return 1; } */
    // int main() { return 2; }
    void main() {
        return;
    }
    """)
    assert CPM.get_main_file([main_file]) == main_file

def test_no_main_file(tmp_path):
    util_file = tmp_path / "util.c"
    util_file.write_text("void helper() {}")
    
    assert CPM.get_main_file([util_file]) is None
