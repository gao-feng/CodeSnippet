#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct {
    long mem_free_kb;
    long mem_avail_kb;
    long rss_kb;
    long pss_kb;
    long maps_count;
    long vm_area_active_objs;
    long vm_area_obj_size;
    long anon_vma_active_objs;
    long anon_vma_obj_size;
    long vmap_area_active_objs;
    long vmap_area_obj_size;
    long dentry_active_objs;
    long dentry_obj_size;
    long inode_active_objs;
    long inode_obj_size;
} snapshot_t;

static long read_value_from_file(const char *path, const char *key) {
    FILE *fp = fopen(path, "r");
    char line[512];
    size_t key_len = strlen(key);

    if (!fp) {
        return -1;
    }

    while (fgets(line, sizeof(line), fp)) {
        if (strncmp(line, key, key_len) == 0) {
            long value = -1;
            if (sscanf(line + key_len, "%ld", &value) == 1) {
                fclose(fp);
                return value;
            }
        }
    }

    fclose(fp);
    return -1;
}

static long count_lines(const char *path) {
    FILE *fp = fopen(path, "r");
    char line[512];
    long count = 0;

    if (!fp) {
        return -1;
    }

    while (fgets(line, sizeof(line), fp)) {
        count++;
    }

    fclose(fp);
    return count;
}

static void read_slab_entry(const char *name, long *active_objs, long *obj_size) {
    FILE *fp = fopen("/proc/slabinfo", "r");
    char line[512];

    *active_objs = -1;
    *obj_size = -1;

    if (!fp) {
        return;
    }

    while (fgets(line, sizeof(line), fp)) {
        char slab_name[128];
        long active = 0;
        long total = 0;
        long size = 0;

        if (sscanf(line, "%127s %ld %ld %ld", slab_name, &active, &total, &size) == 4) {
            if (strcmp(slab_name, name) == 0) {
                *active_objs = active;
                *obj_size = size;
                fclose(fp);
                return;
            }
        }
    }

    fclose(fp);
}

static void take_snapshot(snapshot_t *snap) {
    memset(snap, 0, sizeof(*snap));
    snap->mem_free_kb = read_value_from_file("/proc/meminfo", "MemFree:");
    snap->mem_avail_kb = read_value_from_file("/proc/meminfo", "MemAvailable:");
    snap->rss_kb = read_value_from_file("/proc/self/smaps_rollup", "Rss:");
    snap->pss_kb = read_value_from_file("/proc/self/smaps_rollup", "Pss:");
    snap->maps_count = count_lines("/proc/self/maps");
    read_slab_entry("vm_area_struct", &snap->vm_area_active_objs, &snap->vm_area_obj_size);
    read_slab_entry("anon_vma", &snap->anon_vma_active_objs, &snap->anon_vma_obj_size);
    read_slab_entry("vmap_area", &snap->vmap_area_active_objs, &snap->vmap_area_obj_size);
    read_slab_entry("dentry", &snap->dentry_active_objs, &snap->dentry_obj_size);
    read_slab_entry("inode_cache", &snap->inode_active_objs, &snap->inode_obj_size);
}

static void print_delta(const char *label, const snapshot_t *before, const snapshot_t *after, int iterations) {
    long vm_area_bytes = -1;
    long anon_vma_bytes = -1;
    long vmap_area_bytes = -1;
    long dentry_bytes = -1;
    long inode_bytes = -1;

    if (before->vm_area_active_objs >= 0 && after->vm_area_active_objs >= 0 && after->vm_area_obj_size > 0) {
        vm_area_bytes = (after->vm_area_active_objs - before->vm_area_active_objs) * after->vm_area_obj_size;
    }
    if (before->anon_vma_active_objs >= 0 && after->anon_vma_active_objs >= 0 && after->anon_vma_obj_size > 0) {
        anon_vma_bytes = (after->anon_vma_active_objs - before->anon_vma_active_objs) * after->anon_vma_obj_size;
    }
    if (before->vmap_area_active_objs >= 0 && after->vmap_area_active_objs >= 0 && after->vmap_area_obj_size > 0) {
        vmap_area_bytes = (after->vmap_area_active_objs - before->vmap_area_active_objs) * after->vmap_area_obj_size;
    }
    if (before->dentry_active_objs >= 0 && after->dentry_active_objs >= 0 && after->dentry_obj_size > 0) {
        dentry_bytes = (after->dentry_active_objs - before->dentry_active_objs) * after->dentry_obj_size;
    }
    if (before->inode_active_objs >= 0 && after->inode_active_objs >= 0 && after->inode_obj_size > 0) {
        inode_bytes = (after->inode_active_objs - before->inode_active_objs) * after->inode_obj_size;
    }

    printf("\n== %s ==\n", label);
    printf("iterations: %d\n", iterations);
    printf("self maps delta: %ld\n", after->maps_count - before->maps_count);
    printf("self rss delta: %ld kB\n", after->rss_kb - before->rss_kb);
    printf("self pss delta: %ld kB\n", after->pss_kb - before->pss_kb);
    printf("MemFree delta: %ld kB\n", after->mem_free_kb - before->mem_free_kb);
    printf("MemAvailable delta: %ld kB\n", after->mem_avail_kb - before->mem_avail_kb);

    if (vm_area_bytes >= 0) {
        printf("vm_area_struct delta: %ld objs, about %ld bytes total", after->vm_area_active_objs - before->vm_area_active_objs, vm_area_bytes);
        if (iterations > 0) {
            printf(", about %.2f bytes/load", (double)vm_area_bytes / iterations);
        }
        printf("\n");
    } else {
        printf("vm_area_struct delta: unavailable in /proc/slabinfo on this kernel\n");
    }

    if (anon_vma_bytes >= 0) {
        printf("anon_vma delta: %ld objs, about %ld bytes total", after->anon_vma_active_objs - before->anon_vma_active_objs, anon_vma_bytes);
        if (iterations > 0) {
            printf(", about %.2f bytes/load", (double)anon_vma_bytes / iterations);
        }
        printf(" (related to anonymous VMAs, not a direct dlopen-only cost)\n");
    } else {
        printf("anon_vma delta: unavailable\n");
    }

    if (vmap_area_bytes >= 0) {
        printf("vmap_area delta: %ld objs, about %ld bytes total", after->vmap_area_active_objs - before->vmap_area_active_objs, vmap_area_bytes);
        if (iterations > 0) {
            printf(", about %.2f bytes/load", (double)vmap_area_bytes / iterations);
        }
        printf(" (kernel vmalloc metadata, usually not the main dlopen signal)\n");
    } else {
        printf("vmap_area delta: unavailable\n");
    }

    if (dentry_bytes >= 0) {
        printf("dentry delta: %ld objs, about %ld bytes total", after->dentry_active_objs - before->dentry_active_objs, dentry_bytes);
        if (iterations > 0) {
            printf(", about %.2f bytes/load", (double)dentry_bytes / iterations);
        }
        printf("\n");
    } else {
        printf("dentry delta: unavailable\n");
    }

    if (inode_bytes >= 0) {
        printf("inode_cache delta: %ld objs, about %ld bytes total", after->inode_active_objs - before->inode_active_objs, inode_bytes);
        if (iterations > 0) {
            printf(", about %.2f bytes/load", (double)inode_bytes / iterations);
        }
        printf("\n");
    } else {
        printf("inode_cache delta: unavailable\n");
    }
}

static long file_size_bytes(const char *path) {
    FILE *fp = fopen(path, "rb");
    long size;

    if (!fp) {
        return -1;
    }

    if (fseek(fp, 0, SEEK_END) != 0) {
        fclose(fp);
        return -1;
    }

    size = ftell(fp);
    fclose(fp);
    return size;
}

static void usage(const char *prog) {
    fprintf(stderr, "Usage: %s <path-to-so> [count] [mode]\n", prog);
    fprintf(stderr, "  mode: dlmopen (default) or dlopen\n");
}

int main(int argc, char **argv) {
    const char *lib_path;
    int count = 1000;
    int use_dlmopen = 1;
    void **handles;
    snapshot_t before_load;
    snapshot_t after_load;
    snapshot_t after_close;
    long page_size = sysconf(_SC_PAGESIZE);
    int i;

    if (argc < 2) {
        usage(argv[0]);
        return 1;
    }

    lib_path = argv[1];
    if (argc >= 3) {
        count = atoi(argv[2]);
        if (count <= 0) {
            fprintf(stderr, "count must be > 0\n");
            return 1;
        }
    }
    if (argc >= 4) {
        if (strcmp(argv[3], "dlopen") == 0) {
            use_dlmopen = 0;
        } else if (strcmp(argv[3], "dlmopen") == 0) {
            use_dlmopen = 1;
        } else {
            usage(argv[0]);
            return 1;
        }
    }

    handles = calloc((size_t)count, sizeof(void *));
    if (!handles) {
        perror("calloc");
        return 1;
    }

    printf("library: %s\n", lib_path);
    printf("file size: %ld bytes\n", file_size_bytes(lib_path));
    printf("page size: %ld bytes\n", page_size);
    printf("mode: %s\n", use_dlmopen ? "dlmopen (independent namespaces)" : "dlopen (same namespace, refcount only)");

    take_snapshot(&before_load);

    for (i = 0; i < count; i++) {
        if (use_dlmopen) {
            handles[i] = dlmopen(LM_ID_NEWLM, lib_path, RTLD_NOW | RTLD_LOCAL);
        } else {
            handles[i] = dlopen(lib_path, RTLD_NOW | RTLD_LOCAL);
        }

        if (!handles[i]) {
            fprintf(stderr, "load failed at iteration %d: %s\n", i, dlerror());
            count = i;
            break;
        }
    }

    take_snapshot(&after_load);

    for (i = 0; i < count; i++) {
        if (handles[i] && dlclose(handles[i]) != 0) {
            fprintf(stderr, "dlclose failed at iteration %d: %s\n", i, dlerror());
        }
    }

    take_snapshot(&after_close);

    print_delta("after load", &before_load, &after_load, count);
    print_delta("after close", &before_load, &after_close, count);

    free(handles);
    return 0;
}
