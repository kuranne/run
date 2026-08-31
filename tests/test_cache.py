import pytest
from pathlib import Path
from util.cache import CacheManager

@pytest.fixture(autouse=True)
def isolate_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache_home"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))
    return cache_dir

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

def test_cache_commented_header_dependency(tmp_path):
    manager = CacheManager(project_root=tmp_path)
    source = tmp_path / "main.c"
    source.write_text("""
    // #include "commented_one.h"
    /* #include "commented_two.h" */
    int main() { return 0; }
    """)

    includes = manager._get_c_includes(source)
    assert len(includes) == 0

def test_cache_concurrent_updates(tmp_path):
    import concurrent.futures
    manager = CacheManager(project_root=tmp_path)
    
    files = []
    for i in range(10):
        f = tmp_path / f"file_{i}.c"
        f.write_text(f"int func_{i}() {{ return {i}; }}")
        files.append(f)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(manager.update_cache, files))

    for f in files:
        assert manager.is_changed(f) is False

def test_cache_binary_verification_stem_collision(tmp_path):
    manager = CacheManager(project_root=tmp_path)
    
    c_source = tmp_path / "hello.c"
    c_source.write_text("int main() { return 0; }")
    
    cpp_source = tmp_path / "hello.cpp"
    cpp_source.write_text("int main() { return 1; }")
    
    out_bin = tmp_path / "hello.out"
    
    # 1. Compile hello.cpp to hello.out and update cache
    out_bin.write_bytes(b"binary_compiled_from_cpp")
    manager.update_cache(cpp_source, out_bin)
    
    # Cache hit for cpp
    assert manager.is_changed(cpp_source, out_bin) is False
    
    # 2. hello.c compiles and overwrites hello.out
    out_bin.write_bytes(b"binary_compiled_from_c")
    manager.update_cache(c_source, out_bin)
    
    # Cache hit for c
    assert manager.is_changed(c_source, out_bin) is False
    
    # 3. hello.cpp must now report CHANGED because hello.out was overwritten!
    assert manager.is_changed(cpp_source, out_bin) is True


def test_cache_clear_cleans_objs_dir(tmp_path):
    """Verify that CacheManager.clear() cleans up all cached .o object files and directories."""
    manager = CacheManager(project_root=tmp_path)
    src = tmp_path / "main.c"
    src.write_text("int main() {}")

    obj_path = manager.get_object_path(src)
    obj_path.write_bytes(b"compiled_object_binary")
    manager.update_cache(src, obj_path)

    assert obj_path.exists() is True
    assert manager.objs_dir.exists() is True

    manager.clear()
    assert obj_path.exists() is False
    assert manager.objs_dir.exists() is False


def test_cache_commented_header_at_eof_without_newline(tmp_path):
    """Verify that commented includes at EOF without newline are stripped properly."""
    manager = CacheManager(project_root=tmp_path)
    source = tmp_path / "main.c"
    source.write_bytes(b'int main() { return 0; }\n// #include "never_exist.h"')

    includes = manager._get_c_includes(source)
    assert len(includes) == 0

