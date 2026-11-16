-- Wireshark dissector for PSCP (Publish/Subscribe Chat Protocol), see docs/PROTOCOL.md.
--
-- Install: copy this file to the personal Lua plugins folder and restart Wireshark
--   Windows: %APPDATA%\Wireshark\plugins\        Linux/macOS: ~/.local/lib/wireshark/plugins/
-- (Help > About Wireshark > Folders shows the exact path.)
-- Or one-off: wireshark -X lua_script:tools/pscp.lua capture.pcapng
--
-- Filters: pscp, pscp.type == 7 (PUBLISH), pscp.flags.dup == 1, pscp.topic == "room1"

local pscp = Proto("pscp", "Publish/Subscribe Chat Protocol")

local TYPES = {
    [0x01] = "CONNECT", [0x02] = "CONNACK", [0x03] = "SUBSCRIBE", [0x04] = "SUBACK",
    [0x05] = "UNSUBSCRIBE", [0x06] = "UNSUBACK", [0x07] = "PUBLISH", [0x08] = "PUBACK",
    [0x09] = "PING", [0x0A] = "PONG", [0x0B] = "PRESENCE", [0x0C] = "DISCONNECT", [0x0D] = "ERROR",
}
local STATUS = {
    [0x00] = "OK", [0x10] = "USERNAME_TAKEN", [0x11] = "BAD_VERSION", [0x12] = "MALFORMED",
    [0x13] = "NOT_CONNECTED", [0x20] = "INVALID_TOPIC", [0x21] = "NOT_SUBSCRIBED", [0x30] = "SERVER_FULL",
}
local EVENTS = { [0x01] = "JOIN", [0x02] = "LEAVE", [0x03] = "TIMEOUT" }
local REPLIES = { [0x02] = true, [0x04] = true, [0x06] = true, [0x08] = true, [0x0D] = true }

local f = pscp.fields
f.version     = ProtoField.uint8("pscp.version", "Version", base.HEX)
f.type        = ProtoField.uint8("pscp.type", "Type", base.HEX, TYPES)
f.flags       = ProtoField.uint8("pscp.flags", "Flags", base.HEX)
f.qos         = ProtoField.bool("pscp.flags.qos", "QoS 1 (at least once)", 8, nil, 0x01)
f.dup         = ProtoField.bool("pscp.flags.dup", "DUP (retransmission)", 8, nil, 0x02)
f.session     = ProtoField.bool("pscp.flags.session", "SESSION (CONNECT: resume / CONNACK: present)", 8, nil, 0x04)
f.reserved    = ProtoField.uint8("pscp.flags.reserved", "Reserved", base.HEX, nil, 0xF8)
f.topic_len   = ProtoField.uint8("pscp.topic_len", "Topic Length", base.DEC)
f.msg_id      = ProtoField.uint16("pscp.msg_id", "Message ID", base.DEC)
f.payload_len = ProtoField.uint16("pscp.payload_len", "Payload Length", base.DEC)
f.topic       = ProtoField.string("pscp.topic", "Topic")
f.payload     = ProtoField.bytes("pscp.payload", "Payload")
f.username    = ProtoField.string("pscp.username", "Username")
f.sender      = ProtoField.string("pscp.sender", "Sender")
f.text        = ProtoField.string("pscp.text", "Text")
f.status      = ProtoField.uint8("pscp.status", "Status", base.HEX, STATUS)
f.event       = ProtoField.uint8("pscp.event", "Presence event", base.HEX, EVENTS)

local e_version = ProtoExpert.new("pscp.bad_version", "Unknown PSCP version", expert.group.MALFORMED, expert.severity.WARN)
local e_reserved = ProtoExpert.new("pscp.reserved_set", "Reserved flag bits set", expert.group.MALFORMED, expert.severity.WARN)
pscp.experts = { e_version, e_reserved }

pscp.prefs.port = Pref.uint("Broker TCP port", 5050, "TCP port the PSCP broker listens on")

local HEADER = 8

local function pdu_len(tvb, pinfo, offset)
    return HEADER + tvb(offset + 3, 1):uint() + tvb(offset + 6, 2):uint()
end

local function utf8(range)
    return range:string(ENC_UTF_8)
end

local function short(s)
    if #s > 40 then return s:sub(1, 37) .. "..." end
    return s
end

local info_frame = -1

local function dissect_one(tvb, pinfo, tree)
    local mtype, flags = tvb(1, 1):uint(), tvb(2, 1):uint()
    local tlen, mid, plen = tvb(3, 1):uint(), tvb(4, 2):uint(), tvb(6, 2):uint()
    local name = TYPES[mtype] or string.format("0x%02x", mtype)
    local from_broker = pinfo.src_port == pscp.prefs.port

    local t = tree:add(pscp, tvb(), "PSCP " .. name)
    local h = t:add(tvb(0, HEADER), "Fixed header (8 bytes)")
    local v = h:add(f.version, tvb(0, 1))
    if tvb(0, 1):uint() ~= 1 then v:add_proto_expert_info(e_version) end
    h:add(f.type, tvb(1, 1))
    local ft = h:add(f.flags, tvb(2, 1))
    ft:add(f.qos, tvb(2, 1)); ft:add(f.dup, tvb(2, 1)); ft:add(f.session, tvb(2, 1))
    local r = ft:add(f.reserved, tvb(2, 1))
    if bit.band(flags, 0xF8) ~= 0 then r:add_proto_expert_info(e_reserved) end
    h:add(f.topic_len, tvb(3, 1))
    local id = h:add(f.msg_id, tvb(4, 2))
    if from_broker and (mtype == 0x07 or mtype == 0x0B) then
        id:append_text(" (per-room sequence number)")
    end
    h:add(f.payload_len, tvb(6, 2))

    local summary = name
    local topic = ""
    if tlen > 0 then
        topic = utf8(tvb(HEADER, tlen))
        t:add(f.topic, tvb(HEADER, tlen), topic)
        summary = summary .. " " .. topic
    end
    if mid > 0 then
        summary = summary .. (from_broker and (mtype == 0x07 or mtype == 0x0B) and " seq=" or " id=") .. mid
    end
    if mtype == 0x07 or mtype == 0x03 then summary = summary .. " QoS" .. bit.band(flags, 1) end
    if bit.band(flags, 0x02) ~= 0 then summary = summary .. " DUP" end
    if bit.band(flags, 0x04) ~= 0 then summary = summary .. " SESSION" end

    if plen > 0 then
        local p = tvb(HEADER + tlen, plen)
        local pt = t:add(f.payload, p)
        if mtype == 0x01 then
            pt:add(f.username, p, utf8(p)); summary = summary .. " user=" .. utf8(p)
        elseif REPLIES[mtype] then
            pt:add(f.status, p(0, 1)); summary = summary .. " " .. (STATUS[p(0, 1):uint()] or "?")
        elseif mtype == 0x07 and from_broker and plen >= 1 and plen >= 1 + p(0, 1):uint() then
            local n = p(0, 1):uint()
            local sender, text = utf8(p(1, n)), (plen > 1 + n) and utf8(p(1 + n)) or ""
            pt:add(p(0, 1), "Sender length: " .. n)
            if n > 0 then pt:add(f.sender, p(1, n), sender) end
            if plen > 1 + n then pt:add(f.text, p(1 + n), text) end
            summary = summary .. " " .. sender .. ': "' .. short(text) .. '"'
        elseif mtype == 0x07 then
            pt:add(f.text, p, utf8(p)); summary = summary .. ' "' .. short(utf8(p)) .. '"'
        elseif mtype == 0x0B then
            pt:add(f.event, p(0, 1))
            local user = plen > 1 and utf8(p(1)) or ""
            if plen > 1 then pt:add(f.username, p(1), user) end
            summary = summary .. " " .. (EVENTS[p(0, 1):uint()] or "?") .. " " .. user
        end
    end
    t:append_text(": " .. summary:sub(#name + 2))

    -- several PSCP messages can share one TCP segment: list them all in the Info column
    if info_frame ~= pinfo.number then
        info_frame = pinfo.number
        pinfo.cols.info:set(summary)
    else
        pinfo.cols.info:append(" | " .. summary)
    end
    return tvb:len()
end

function pscp.dissector(tvb, pinfo, tree)
    pinfo.cols.protocol = "PSCP"
    dissect_tcp_pdus(tvb, tree, HEADER, pdu_len, dissect_one, true)
    return tvb:len()
end

local registered_port = pscp.prefs.port
DissectorTable.get("tcp.port"):add(registered_port, pscp)

function pscp.prefs_changed()
    if registered_port ~= pscp.prefs.port then
        DissectorTable.get("tcp.port"):remove(registered_port, pscp)
        registered_port = pscp.prefs.port
        DissectorTable.get("tcp.port"):add(registered_port, pscp)
    end
end
