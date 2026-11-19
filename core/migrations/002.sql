CREATE INDEX events_topic_seq ON events(topic, seq);
CREATE INDEX radio_plugin_result_seq ON events(json_extract(data,'$.plugin'), seq)
    WHERE topic='radio/result' AND json_extract(data,'$.ok')=1;
PRAGMA user_version=2;
