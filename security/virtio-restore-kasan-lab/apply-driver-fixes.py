#!/usr/bin/env python3
"""Apply narrow failed-restore lifetime fixes to a pinned Linux checkout.

This is a CI overlay only: it edits the temporary torvalds/linux checkout
inside the workflow runner and never changes upstream source directly.
"""
from pathlib import Path
import sys


def replace_once(root: Path, path: str, old: str, new: str, label: str) -> None:
    file = root / path
    source = file.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise SystemExit(
            f"DRIVER_FIX_ERROR={label}: expected one anchor in {path}, found {count}"
        )
    file.write_text(source.replace(old, new, 1), encoding="utf-8")
    print(f"DRIVER_FIX_APPLIED={label}")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <linux-source-tree>", file=sys.stderr)
        return 64
    root = Path(sys.argv[1])

    # virtio-snd: do not retain queue pointers after freeze has deleted them.
    replace_once(
        root,
        "sound/virtio/virtio_card.c",
        """\tvirtio_reset_device(vdev);
\tvdev->config->del_vqs(vdev);

\tfor (i = 0; i < snd->nsubstreams; ++i)""",
        """\tif (snd->vqs_initialized) {
\t\tvirtio_reset_device(vdev);
\t\tvdev->config->del_vqs(vdev);
\t\tsnd->vqs_initialized = false;
\t}
\tfor (i = 0; i < VIRTIO_SND_VQ_MAX; ++i)
\t\tsnd->queues[i].vqueue = NULL;

\tfor (i = 0; i < snd->nsubstreams; ++i)""",
        "virtio-snd-clear-queues-on-freeze",
    )


    replace_once(
        root,
        "sound/virtio/virtio_card.h",
        """struct virtio_snd {
\tstruct virtio_device *vdev;
\tstruct virtio_snd_queue queues[VIRTIO_SND_VQ_MAX];""",
        """struct virtio_snd {
\tstruct virtio_device *vdev;
\tbool vqs_initialized;
\tstruct virtio_snd_queue queues[VIRTIO_SND_VQ_MAX];""",
        "virtio-snd-vqs-initialized-state-field",
    )
    replace_once(
        root,
        "sound/virtio/virtio_card.c",
        """\tfor (i = 0; i < VIRTIO_SND_VQ_MAX; ++i)
\t\tsnd->queues[i].vqueue = vqs[i];""",
        """\tsnd->vqs_initialized = true;
\tfor (i = 0; i < VIRTIO_SND_VQ_MAX; ++i)
\t\tsnd->queues[i].vqueue = vqs[i];""",
        "virtio-snd-mark-vqs-live-after-find",
    )
    replace_once(
        root,
        "sound/virtio/virtio_card.c",
        """\tvirtio_reset_device(vdev);
\tvdev->config->del_vqs(vdev);

\tfor (i = 0; snd->substreams && i < snd->nsubstreams; ++i)""",
        """\tif (snd->vqs_initialized) {
\t\tvirtio_reset_device(vdev);
\t\tvdev->config->del_vqs(vdev);
\t\tsnd->vqs_initialized = false;
\t}
\tfor (i = 0; i < VIRTIO_SND_VQ_MAX; ++i)
\t\tsnd->queues[i].vqueue = NULL;

\tfor (i = 0; snd->substreams && i < snd->nsubstreams; ++i)""",
        "virtio-snd-remove-only-deletes-live-vqs",
    )

    # virtio-crypto: free helpers must leave an explicit empty state, and
    # cleanup must tolerate the restore path having failed before work items
    # or queues were initialized.
    replace_once(
        root,
        "drivers/crypto/virtio/virtio_crypto_core.c",
        """static void virtcrypto_clean_affinity(struct virtio_crypto *vi, long hcpu)
{
\tint i;

\tif (vi->affinity_hint_set) {""",
        """static void virtcrypto_clean_affinity(struct virtio_crypto *vi, long hcpu)
{
\tint i;

\tif (!vi->data_vq) {
\t\tvi->affinity_hint_set = false;
\t\treturn;
\t}

\tif (vi->affinity_hint_set) {""",
        "virtio-crypto-clean-affinity-null-guard",
    )
    replace_once(
        root,
        "drivers/crypto/virtio/virtio_crypto_core.c",
        """static void virtcrypto_set_affinity(struct virtio_crypto *vcrypto)
{""",
        """static void virtcrypto_cancel_done_work(struct virtio_crypto *vcrypto)
{
\tint i;

\tif (!vcrypto->data_vq)
\t\treturn;

\tvirtio_synchronize_cbs(vcrypto->vdev);
\tfor (i = 0; i < vcrypto->max_data_queues; i++)
\t\tcancel_work_sync(&vcrypto->data_vq[i].done_work);
}

static void virtcrypto_set_affinity(struct virtio_crypto *vcrypto)
{""",
        "virtio-crypto-synchronize-and-cancel-done-work",
    )
    replace_once(
        root,
        "drivers/crypto/virtio/virtio_crypto_core.c",
        """static void virtcrypto_free_queues(struct virtio_crypto *vi)
{
\tkfree(vi->data_vq);
}""",
        """static void virtcrypto_free_queues(struct virtio_crypto *vi)
{
\tkfree(vi->data_vq);
\tvi->data_vq = NULL;
\tvi->ctrl_vq = NULL;
\tvi->affinity_hint_set = false;
\tvi->vqs_initialized = false;
}""",
        "virtio-crypto-null-freed-queue-state",
    )
    replace_once(
        root,
        "drivers/crypto/virtio/virtio_crypto_core.c",
        """static void virtcrypto_clear_crypto_engines(struct virtio_crypto *vcrypto)
{
\tu32 i;

\tfor (i = 0; i < vcrypto->max_data_queues; i++)""",
        """static void virtcrypto_clear_crypto_engines(struct virtio_crypto *vcrypto)
{
\tu32 i;

\tif (!vcrypto->data_vq)
\t\treturn;

\tvirtcrypto_cancel_done_work(vcrypto);
\tfor (i = 0; i < vcrypto->max_data_queues; i++)""",
        "virtio-crypto-clear-engines-null-guard",
    )
    replace_once(
        root,
        "drivers/crypto/virtio/virtio_crypto_core.c",
        """static void virtcrypto_free_unused_reqs(struct virtio_crypto *vcrypto)
{
\tstruct virtio_crypto_request *vc_req;
\tint i;
\tstruct virtqueue *vq;

\tfor (i = 0; i < vcrypto->max_data_queues; i++)""",
        """static void virtcrypto_free_unused_reqs(struct virtio_crypto *vcrypto)
{
\tstruct virtio_crypto_request *vc_req;
\tint i;
\tstruct virtqueue *vq;

\tif (!vcrypto->data_vq)
\t\treturn;

\tfor (i = 0; i < vcrypto->max_data_queues; i++)""",
        "virtio-crypto-free-requests-null-guard",
    )
    replace_once(
        root,
        "drivers/crypto/virtio/virtio_crypto_core.c",
        """\tfor (i = 0; i < vcrypto->max_data_queues; i++)
\t\tcancel_work_sync(&vcrypto->data_vq[i].done_work);""",
        """\tif (vcrypto->data_vq)
\t\tfor (i = 0; i < vcrypto->max_data_queues; i++)
\t\t\tcancel_work_sync(&vcrypto->data_vq[i].done_work);""",
        "virtio-crypto-cancel-only-initialized-work",
    )



    replace_once(
        root,
        "drivers/crypto/virtio/virtio_crypto_core.c",
        """\tif (vcrypto->data_vq)
\t\tfor (i = 0; i < vcrypto->max_data_queues; i++)
\t\t\tcancel_work_sync(&vcrypto->data_vq[i].done_work);
\tvirtio_reset_device(vdev);
\tvirtcrypto_free_unused_reqs(vcrypto);""",
        """\tif (vcrypto->data_vq)
\t\tfor (i = 0; i < vcrypto->max_data_queues; i++)
\t\t\tcancel_work_sync(&vcrypto->data_vq[i].done_work);
\tvirtio_reset_device(vdev);
\tvirtcrypto_cancel_done_work(vcrypto);
\tvirtcrypto_free_unused_reqs(vcrypto);""",
        "virtio-crypto-remove-drains-work-after-reset",
    )
    replace_once(
        root,
        "drivers/crypto/virtio/virtio_crypto_core.c",
        """\tvirtio_reset_device(vdev);
\tvirtcrypto_free_unused_reqs(vcrypto);
\tif (virtcrypto_dev_started(vcrypto))""",
        """\tvirtio_reset_device(vdev);
\tvirtcrypto_cancel_done_work(vcrypto);
\tvirtcrypto_free_unused_reqs(vcrypto);
\tif (virtcrypto_dev_started(vcrypto))""",
        "virtio-crypto-freeze-drains-work-before-detach",
    )
    replace_once(
        root,
        "drivers/crypto/virtio/virtio_crypto_common.h",
        """\t/* Does the affinity hint is set for virtqueues? */
\tbool affinity_hint_set;""",
        """\t/* Does the affinity hint is set for virtqueues? */
\tbool affinity_hint_set;
\tbool vqs_initialized;""",
        "virtio-crypto-vqs-initialized-state-field",
    )
    replace_once(
        root,
        "drivers/crypto/virtio/virtio_crypto_core.c",
        """\tint i, total_vqs;
\tstruct device *dev = &vi->vdev->dev;""",
        """\tint i, j, total_vqs;
\tstruct device *dev = &vi->vdev->dev;""",
        "virtio-crypto-initialization-rollback-index",
    )
    replace_once(
        root,
        "drivers/crypto/virtio/virtio_crypto_core.c",
        """\tret = virtio_find_vqs(vi->vdev, total_vqs, vqs, vqs_info, NULL);
\tif (ret)
\t\tgoto err_find;

\tvi->ctrl_vq = vqs[total_vqs - 1];""",
        """\tret = virtio_find_vqs(vi->vdev, total_vqs, vqs, vqs_info, NULL);
\tif (ret)
\t\tgoto err_find;

\tvi->vqs_initialized = true;
\tvi->ctrl_vq = vqs[total_vqs - 1];""",
        "virtio-crypto-mark-vqs-live-after-find",
    )
    replace_once(
        root,
        "drivers/crypto/virtio/virtio_crypto_core.c",
        """err_engine:
err_find:
\tkfree(vqs_info);""",
        """err_engine:
\tvirtio_reset_device(vi->vdev);
\tvirtio_synchronize_cbs(vi->vdev);
\tfor (j = 0; j < i; j++) {
\t\tcancel_work_sync(&vi->data_vq[j].done_work);
\t\tif (vi->data_vq[j].engine) {
\t\t\tcrypto_engine_exit(vi->data_vq[j].engine);
\t\t\tvi->data_vq[j].engine = NULL;
\t\t}
\t}
\tvi->vdev->config->del_vqs(vi->vdev);
\tvi->vqs_initialized = false;
\tvi->ctrl_vq = NULL;
err_find:
\tkfree(vqs_info);""",
        "virtio-crypto-rollback-partial-engine-initialization",
    )

    replace_once(
        root,
        "drivers/crypto/virtio/virtio_crypto_core.c",
        """static void virtcrypto_del_vqs(struct virtio_crypto *vcrypto)
{
\tstruct virtio_device *vdev = vcrypto->vdev;

\tvirtcrypto_clean_affinity(vcrypto, -1);

\tvdev->config->del_vqs(vdev);

\tvirtcrypto_free_queues(vcrypto);
}""",
        """static void virtcrypto_del_vqs(struct virtio_crypto *vcrypto)
{
\tstruct virtio_device *vdev = vcrypto->vdev;

\tvirtcrypto_clean_affinity(vcrypto, -1);

\tif (vcrypto->vqs_initialized) {
\t\tvirtcrypto_cancel_done_work(vcrypto);
\t\tvdev->config->del_vqs(vdev);
\t\tvcrypto->vqs_initialized = false;
\t}

\tvirtcrypto_free_queues(vcrypto);
}""",
        "virtio-crypto-del-only-live-vqs",
    )
    # virtio-console: free-on-error was followed by unconditional freeing at
    # remove; also clear the per-port pointers when the queues are deleted.
    replace_once(
        root,
        "drivers/char/virtio_console.c",
        """free:
\tkfree(portdev->out_vqs);
\tkfree(portdev->in_vqs);
\tkfree(vqs_info);""",
        """free:
\tkfree(portdev->out_vqs);
\tportdev->out_vqs = NULL;
\tkfree(portdev->in_vqs);
\tportdev->in_vqs = NULL;
\tkfree(vqs_info);""",
        "virtio-console-null-arrays-after-init-error",
    )
    replace_once(
        root,
        "drivers/char/virtio_console.c",
        """static struct port_buffer *get_inbuf(struct port *port)
{
\tstruct port_buffer *buf;
\tunsigned int len;

\tif (port->inbuf)""",
        """static struct port_buffer *get_inbuf(struct port *port)
{
\tstruct port_buffer *buf;
\tunsigned int len;

\tif (!port->in_vq)
\t\treturn NULL;

\tif (port->inbuf)""",
        "virtio-console-get-inbuf-null-guard",
    )
    replace_once(
        root,
        "drivers/char/virtio_console.c",
        """\tif (!port->portdev) {
\t\t/* Device has been unplugged.  vqs are already gone. */
\t\treturn;
\t}
\tbuf = get_inbuf(port);""",
        """\tif (!port->portdev) {
\t\t/* Device has been unplugged.  vqs are already gone. */
\t\treturn;
\t}
\tif (!port->in_vq) {
\t\tif (port->inbuf)
\t\t\tfree_buf(port->inbuf, false);
\t\tport->inbuf = NULL;
\t\treturn;
\t}
\tbuf = get_inbuf(port);""",
        "virtio-console-discard-without-live-input-queue",
    )
    replace_once(
        root,
        "drivers/char/virtio_console.c",
        """\tif (!port->portdev) {
\t\t/* Device has been unplugged.  vqs are already gone. */
\t\treturn;
\t}
\twhile ((buf = virtqueue_get_buf(port->out_vq, &len)))""",
        """\tif (!port->portdev || !port->out_vq) {
\t\t/* Device has been unplugged or its queues are not initialized. */
\t\treturn;
\t}
\twhile ((buf = virtqueue_get_buf(port->out_vq, &len)))""",
        "virtio-console-reclaim-without-live-output-queue",
    )
    replace_once(
        root,
        "drivers/char/virtio_console.c",
        """\tif (!use_multiport(portdev))
\t\treturn 0;

\tvq = portdev->c_ovq;""",
        """\tif (!use_multiport(portdev) || !portdev->c_ovq)
\t\treturn 0;

\tvq = portdev->c_ovq;""",
        "virtio-console-control-send-null-guard",
    )
    replace_once(
        root,
        "drivers/char/virtio_console.c",
        """static void remove_vqs(struct ports_device *portdev)
{
\tstruct virtqueue *vq;
\tbool multiport = use_multiport(portdev);""",
        """static void remove_vqs(struct ports_device *portdev)
{
\tstruct virtqueue *vq;
\tstruct port *port;
\tbool multiport = use_multiport(portdev);""",
        "virtio-console-remove-vqs-port-iterator",
    )
    replace_once(
        root,
        "drivers/char/virtio_console.c",
        """\tportdev->vdev->config->del_vqs(portdev->vdev);
\tkfree(portdev->in_vqs);
\tkfree(portdev->out_vqs);
}""",
        """\tif (portdev->vqs_initialized) {
\t\tportdev->vdev->config->del_vqs(portdev->vdev);
\t\tportdev->vqs_initialized = false;
\t}

\tlist_for_each_entry(port, &portdev->ports, list) {
\t\tspin_lock_irq(&port->inbuf_lock);
\t\tport->in_vq = NULL;
\t\tspin_unlock_irq(&port->inbuf_lock);
\t\tspin_lock_irq(&port->outvq_lock);
\t\tport->out_vq = NULL;
\t\tspin_unlock_irq(&port->outvq_lock);
\t}
\tportdev->c_ivq = NULL;
\tportdev->c_ovq = NULL;
\tkfree(portdev->in_vqs);
\tportdev->in_vqs = NULL;
\tkfree(portdev->out_vqs);
\tportdev->out_vqs = NULL;
}""",
        "virtio-console-clear-queue-pointers-after-delete",
    )


    replace_once(
        root,
        "drivers/char/virtio_console.c",
        """\tbool pm_freezing;""",
        """\tbool pm_freezing;
\tbool vqs_initialized;""",
        "virtio-console-vqs-initialized-state-field",
    )
    replace_once(
        root,
        "drivers/char/virtio_console.c",
        """\t\t}
\t}
\tkfree(vqs_info);
\tkfree(vqs);

\treturn 0;

free:""",
        """\t\t}
\t}
\tportdev->vqs_initialized = true;
\tkfree(vqs_info);
\tkfree(vqs);

\treturn 0;

free:""",
        "virtio-console-mark-vqs-live-after-find",
    )
    # virtio-net: track CPU hotplug registration explicitly, and make a
    # failed init_vqs unwind leave queue arrays in a safe empty state.
    replace_once(
        root,
        "drivers/net/virtio_net.c",
        """\t/* CPU hotplug instances for online & dead */
\tstruct hlist_node node;
\tstruct hlist_node node_dead;""",
        """\t/* CPU hotplug instances for online & dead */
\tstruct hlist_node node;
\tstruct hlist_node node_dead;
\tbool cpu_notif_registered;""",
        "virtio-net-registration-state-field",
    )
    replace_once(
        root,
        "drivers/net/virtio_net.c",
        """static int virtnet_cpu_notif_add(struct virtnet_info *vi)
{
\tint ret;

\tret = cpuhp_state_add_instance_nocalls(virtionet_online, &vi->node);
\tif (ret)
\t\treturn ret;
\tret = cpuhp_state_add_instance_nocalls(CPUHP_VIRT_NET_DEAD,
\t\t\t\t\t       &vi->node_dead);
\tif (!ret)
\t\treturn ret;
\tcpuhp_state_remove_instance_nocalls(virtionet_online, &vi->node);
\treturn ret;
}

static void virtnet_cpu_notif_remove(struct virtnet_info *vi)
{
\tcpuhp_state_remove_instance_nocalls(virtionet_online, &vi->node);
\tcpuhp_state_remove_instance_nocalls(CPUHP_VIRT_NET_DEAD,
\t\t\t\t\t    &vi->node_dead);
}""",
        """static int virtnet_cpu_notif_add(struct virtnet_info *vi)
{
\tint ret;

\tif (vi->cpu_notif_registered)
\t\treturn -EBUSY;

\tret = cpuhp_state_add_instance_nocalls(virtionet_online, &vi->node);
\tif (ret)
\t\treturn ret;
\tret = cpuhp_state_add_instance_nocalls(CPUHP_VIRT_NET_DEAD,
\t\t\t\t\t       &vi->node_dead);
\tif (ret) {
\t\tcpuhp_state_remove_instance_nocalls(virtionet_online, &vi->node);
\t\treturn ret;
\t}

\tvi->cpu_notif_registered = true;
\treturn 0;
}

static void virtnet_cpu_notif_remove(struct virtnet_info *vi)
{
\tif (!vi->cpu_notif_registered)
\t\treturn;

\tcpuhp_state_remove_instance_nocalls(virtionet_online, &vi->node);
\tcpuhp_state_remove_instance_nocalls(CPUHP_VIRT_NET_DEAD,
\t\t\t\t\t    &vi->node_dead);
\tvi->cpu_notif_registered = false;
}""",
        "virtio-net-guard-cpu-hotplug-notifier-lifetime",
    )
    replace_once(
        root,
        "drivers/net/virtio_net.c",
        """\tfor (i = 0; i < vi->max_queue_pairs; i++) {
\t\tvirtnet_disable_queue_pair(vi, i);
\t\tvirtnet_cancel_dim(vi, &vi->rq[i].dim);
\t}

\tnetif_carrier_off(dev);""",
        """\tif (vi->rq && vi->sq) {
\t\tfor (i = 0; i < vi->max_queue_pairs; i++) {
\t\t\tvirtnet_disable_queue_pair(vi, i);
\t\t\tvirtnet_cancel_dim(vi, &vi->rq[i].dim);
\t\t}
\t}

\tnetif_carrier_off(dev);""",
        "virtio-net-close-null-queue-guard",
    )
    replace_once(
        root,
        "drivers/net/virtio_net.c",
        """err_rq:
\tkfree(vi->sq);
err_sq:
\tkfree(vi->ctrl);
err_ctrl:
\treturn -ENOMEM;""",
        """err_rq:
\tkfree(vi->sq);
\tvi->sq = NULL;
err_sq:
\tkfree(vi->ctrl);
\tvi->ctrl = NULL;
err_ctrl:
\treturn -ENOMEM;""",
        "virtio-net-null-partial-allocation-failures",
    )
    replace_once(
        root,
        "drivers/net/virtio_net.c",
        """static void virtnet_free_queues(struct virtnet_info *vi)
{
\tint i;

\tfor (i = 0; i < vi->max_queue_pairs; i++) {
\t\t__netif_napi_del(&vi->rq[i].napi);
\t\t__netif_napi_del(&vi->sq[i].napi);
\t}

\t/* We called __netif_napi_del(),
\t * we need to respect an RCU grace period before freeing vi->rq
\t */
\tsynchronize_net();

\tkfree(vi->rq);
\tkfree(vi->sq);
\tkfree(vi->ctrl);
}""",
        """static void virtnet_free_queues(struct virtnet_info *vi)
{
\tint i;

\tfor (i = 0; i < vi->max_queue_pairs; i++) {
\t\tif (vi->rq)
\t\t\t__netif_napi_del(&vi->rq[i].napi);
\t\tif (vi->sq)
\t\t\t__netif_napi_del(&vi->sq[i].napi);
\t}

\t/* We called __netif_napi_del(),
\t * we need to respect an RCU grace period before freeing vi->rq
\t */
\tif (vi->rq || vi->sq)
\t\tsynchronize_net();

\tkfree(vi->rq);
\tvi->rq = NULL;
\tkfree(vi->sq);
\tvi->sq = NULL;
\tkfree(vi->ctrl);
\tvi->ctrl = NULL;
\tvi->cvq = NULL;
}""",
        "virtio-net-free-queues-idempotent-empty-state",
    )
    replace_once(
        root,
        "drivers/net/virtio_net.c",
        """static void _free_receive_bufs(struct virtnet_info *vi)
{
\tstruct bpf_prog *old_prog;
\tint i;

\tfor (i = 0; i < vi->max_queue_pairs; i++)""",
        """static void _free_receive_bufs(struct virtnet_info *vi)
{
\tstruct bpf_prog *old_prog;
\tint i;

\tif (!vi->rq)
\t\treturn;

\tfor (i = 0; i < vi->max_queue_pairs; i++)""",
        "virtio-net-free-receive-bufs-null-guard",
    )
    replace_once(
        root,
        "drivers/net/virtio_net.c",
        """static void free_receive_bufs(struct virtnet_info *vi)
{
\trtnl_lock();""",
        """static void free_receive_bufs(struct virtnet_info *vi)
{
\tif (!vi->rq)
\t\treturn;

\trtnl_lock();""",
        "virtio-net-free-receive-bufs-wrapper-null-guard",
    )
    replace_once(
        root,
        "drivers/net/virtio_net.c",
        """static void free_unused_bufs(struct virtnet_info *vi)
{
\tvoid *buf;
\tint i;

\tfor (i = 0; i < vi->max_queue_pairs; i++)""",
        """static void free_unused_bufs(struct virtnet_info *vi)
{
\tvoid *buf;
\tint i;

\tif (!vi->rq || !vi->sq)
\t\treturn;

\tfor (i = 0; i < vi->max_queue_pairs; i++)""",
        "virtio-net-free-unused-bufs-null-guard",
    )
    replace_once(
        root,
        "drivers/net/virtio_net.c",
        """static void virtnet_destroy_page_pools(struct virtnet_info *vi)
{
\tint i;

\tfor (i = 0; i < vi->max_queue_pairs; i++)""",
        """static void virtnet_destroy_page_pools(struct virtnet_info *vi)
{
\tint i;

\tif (!vi->rq)
\t\treturn;

\tfor (i = 0; i < vi->max_queue_pairs; i++)""",
        "virtio-net-destroy-page-pools-null-guard",
    )

    # virtio-input: failed restore leaves the pre-freeze queue handles stale.
    # Clear them after del_vqs(), and do not detach buffers through a dead queue.
    replace_once(
        root,
        "drivers/virtio/virtio_input.c",
        """\tvirtio_reset_device(vdev);
\tinput_unregister_device(vi->idev);
\twhile ((buf = virtqueue_detach_unused_buf(vi->sts)) != NULL)
\t\tkfree(buf);
\tvdev->config->del_vqs(vdev);
\tkfree(vi);""",
        """\tvirtio_reset_device(vdev);
\tinput_unregister_device(vi->idev);
\tif (vi->sts)
\t\twhile ((buf = virtqueue_detach_unused_buf(vi->sts)) != NULL)
\t\t\tkfree(buf);
\tvdev->config->del_vqs(vdev);
\tkfree(vi);""",
        "virtio-input-remove-only-detaches-live-status-queue",
    )
    replace_once(
        root,
        "drivers/virtio/virtio_input.c",
        """\twhile ((buf = virtqueue_detach_unused_buf(vi->sts)) != NULL)
\t\tkfree(buf);
\tvdev->config->del_vqs(vdev);
\treturn 0;""",
        """\twhile ((buf = virtqueue_detach_unused_buf(vi->sts)) != NULL)
\t\tkfree(buf);
\tvdev->config->del_vqs(vdev);
\tvi->evt = NULL;
\tvi->sts = NULL;
\treturn 0;""",
        "virtio-input-clear-queue-pointers-after-freeze",
    )

    # virtio-gpu: teardown must not reclaim buffers through queue objects
    # already destroyed by the PCI freeze path.
    replace_once(
        root,
        "drivers/gpu/drm/virtio/virtgpu_vq.c",
        """\twhile ((vbuf = virtqueue_detach_unused_buf(vgdev->ctrlq.vq))) {
\t\tif (vbuf->objs)
\t\t\tvirtio_gpu_array_put_free(vbuf->objs);
\t\tif (vbuf->resp_cb_data)
\t\t\tvirtio_gpu_cleanup_object(vbuf->resp_cb_data);
\t\tfree_vbuf(vgdev, vbuf);
\t}
\twhile ((vbuf = virtqueue_detach_unused_buf(vgdev->cursorq.vq)))
\t\tfree_vbuf(vgdev, vbuf);""",
        """\tif (vgdev->ctrlq.vq) {
\t\twhile ((vbuf = virtqueue_detach_unused_buf(vgdev->ctrlq.vq))) {
\t\t\tif (vbuf->objs)
\t\t\t\tvirtio_gpu_array_put_free(vbuf->objs);
\t\t\tif (vbuf->resp_cb_data)
\t\t\t\tvirtio_gpu_cleanup_object(vbuf->resp_cb_data);
\t\t\tfree_vbuf(vgdev, vbuf);
\t\t}
\t}
\tif (vgdev->cursorq.vq) {
\t\twhile ((vbuf = virtqueue_detach_unused_buf(vgdev->cursorq.vq)))
\t\t\tfree_vbuf(vgdev, vbuf);
\t}""",
        "virtio-gpu-reclaim-only-live-queues",
    )
    replace_once(
        root,
        "drivers/gpu/drm/virtio/virtgpu_drv.c",
        """\tvirtio_reset_device(vdev);
\tvdev->config->del_vqs(vdev);

\treturn 0;

err_resume:""",
        """\tvirtio_reset_device(vdev);
\tvdev->config->del_vqs(vdev);
\tvgdev->ctrlq.vq = NULL;
\tvgdev->cursorq.vq = NULL;

\treturn 0;

err_resume:""",
        "virtio-gpu-clear-queue-pointers-after-freeze",
    )

    # virtio-net: rtnetlink may query stats while unregister_netdev() runs
    # after a failed restore has already destroyed the RX/TX queue arrays.
    replace_once(
        root,
        "drivers/net/virtio_net.c",
        """static void virtnet_stats(struct net_device *dev,
\t\t\t  struct rtnl_link_stats64 *tot)
{
\tstruct virtnet_info *vi = netdev_priv(dev);
\tunsigned int start;
\tint i;

\tfor (i = 0; i < vi->max_queue_pairs; i++) {""",
        """static void virtnet_stats(struct net_device *dev,
\t\t\t  struct rtnl_link_stats64 *tot)
{
\tstruct virtnet_info *vi = netdev_priv(dev);
\tunsigned int start;
\tint i;

\tif (unlikely(!vi->rq || !vi->sq))
\t\tgoto out;

\tfor (i = 0; i < vi->max_queue_pairs; i++) {""",
        "virtio-net-stats-guard-after-failed-restore",
    )
    replace_once(
        root,
        "drivers/net/virtio_net.c",
        """\t\ttot->tx_errors  += terrors;
\t}

\ttot->tx_dropped = DEV_STATS_READ(dev, tx_dropped);""",
        """\t\ttot->tx_errors  += terrors;
\t}

out:
\ttot->tx_dropped = DEV_STATS_READ(dev, tx_dropped);""",
        "virtio-net-stats-empty-queue-exit",
    )

    # virtio-input: avoid calling del_vqs twice when remove follows freeze
    # and restore failed before virtinput_init_vqs() replaced the pointers.
    replace_once(
        root,
        "drivers/virtio/virtio_input.c",
        """\tif (vi->sts)
\t\twhile ((buf = virtqueue_detach_unused_buf(vi->sts)) != NULL)
\t\t\tkfree(buf);
\tvdev->config->del_vqs(vdev);
\tkfree(vi);""",
        """\tif (vi->sts)
\t\twhile ((buf = virtqueue_detach_unused_buf(vi->sts)) != NULL)
\t\t\tkfree(buf);
\tif (vi->evt || vi->sts)
\t\tvdev->config->del_vqs(vdev);
\tkfree(vi);""",
        "virtio-input-remove-skips-duplicate-del-vqs",
    )

    # virtio-gpu: do not reset/reclaim/delete queues a second time after
    # freeze has already destroyed them and restore failed before recreating.
    replace_once(
        root,
        "drivers/gpu/drm/virtio/virtgpu_kms.c",
        """\tvirtio_reset_device(vgdev->vdev);
\tvirtio_gpu_reclaim_vbufs(vgdev);
\tvgdev->vdev->config->del_vqs(vgdev->vdev);
\tmutex_destroy(&vgdev->obj_restore_lock);""",
        """\tif (vgdev->ctrlq.vq || vgdev->cursorq.vq) {
\t\tvirtio_reset_device(vgdev->vdev);
\t\tvirtio_gpu_reclaim_vbufs(vgdev);
\t\tvgdev->vdev->config->del_vqs(vgdev->vdev);
\t\tvgdev->ctrlq.vq = NULL;
\t\tvgdev->cursorq.vq = NULL;
\t}
\tmutex_destroy(&vgdev->obj_restore_lock);""",
        "virtio-gpu-deinit-skips-deleted-vqs",
    )

    # virtio-net: queue arrays can be gone while the netdev is still
    # registered after a failed restore. Track whether the transport actually
    # owns live queues so final remove does not call del_vqs a second time.
    replace_once(
        root,
        "drivers/net/virtio_net.c",
        """\t/* CPU hotplug instances for online & dead */
\tstruct hlist_node node;
\tstruct hlist_node node_dead;
\tbool cpu_notif_registered;""",
        """\t/* CPU hotplug instances for online & dead */
\tstruct hlist_node node;
\tstruct hlist_node node_dead;
\tbool cpu_notif_registered;
\tbool vqs_initialized;""",
        "virtio-net-vqs-initialized-state-field",
    )
    replace_once(
        root,
        "drivers/net/virtio_net.c",
        """\tfor (i = 0; i < vi->max_queue_pairs; i++) {
\t\tvi->rq[i].vq = vqs[rxq2vq(i)];
\t\tvi->rq[i].min_buf_len = mergeable_min_buf_len(vi, vi->rq[i].vq);
\t\tvi->sq[i].vq = vqs[txq2vq(i)];
\t}
\t/* run here: ret == 0. */""",
        """\tfor (i = 0; i < vi->max_queue_pairs; i++) {
\t\tvi->rq[i].vq = vqs[rxq2vq(i)];
\t\tvi->rq[i].min_buf_len = mergeable_min_buf_len(vi, vi->rq[i].vq);
\t\tvi->sq[i].vq = vqs[txq2vq(i)];
\t}
\tvi->vqs_initialized = true;
\t/* run here: ret == 0. */""",
        "virtio-net-mark-vqs-live-after-successful-find",
    )
    replace_once(
        root,
        "drivers/net/virtio_net.c",
        """static void virtnet_del_vqs(struct virtnet_info *vi)
{
\tstruct virtio_device *vdev = vi->vdev;

\tvirtnet_clean_affinity(vi);

\tvdev->config->del_vqs(vdev);

\tvirtnet_free_queues(vi);
}""",
        """static void virtnet_del_vqs(struct virtnet_info *vi)
{
\tstruct virtio_device *vdev = vi->vdev;

\tvirtnet_clean_affinity(vi);

\tif (vi->vqs_initialized) {
\t\tvdev->config->del_vqs(vdev);
\t\tvi->vqs_initialized = false;
\t}

\tvirtnet_free_queues(vi);
}""",
        "virtio-net-del-only-live-vqs",
    )

    print("DRIVER_FIX_RESULT=all-six-driver-overlay-groups-applied-with-idempotent-transport-state-and-work-drain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
