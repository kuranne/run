import pytest
from pathlib import Path
from util.cache import CacheManager

def test_cache_init_and_hash(tmp_path):
    manager = CacheManager(project_root=tmp_path)
    
    test_file = tmp_path / "test.c"
    test_file.write_text("int main() {}")
    
    assert manager.is_changed(test_file) == True
    
    manager.update_cache(test_file)
    assert manager.is_changed(test_file) == False
    
    # Modify file
    test_file.write_text("int main() { return 0; }")
    assert manager.is_changed(test_file) == True
    
def test_cache_clear(tmp_path):
    manager = CacheManager(project_root=tmp_path)
    test_file = tmp_path / "test.c"
    test_file.write_text("code")
    
    manager.update_cache(test_file)
    assert not manager.is_changed(test_file)
    
    manager.clear()
    assert manager.is_changed(test_file)

def test_cache_header_dependency(tmp_path):
    manager = CacheManager(project_root=tmp_path)
    header = tmp_path / "helper.h"
    header.write_text("int get_val() { return 1; }")

    source = tmp_path / "main.c"
    source.write_text('#include "helper.h"\nint main() { return get_val(); }')

    assert manager.is_changed(source) is True
    manager.update_cache(source)
    assert manager.is_changed(source) is False

    # Modify header file only
    header.write_text("int get_val() { return 2; }")
    assert manager.is_changed(source) is True
