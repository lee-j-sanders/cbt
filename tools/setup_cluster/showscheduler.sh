
for i in /sys/block/sd*//queue/scheduler ; do 
  echo [SD: $i]
  cat $i
done
for i in /sys/block/dm*/queue/scheduler ; do 
  echo [DM $i] 
  cat $i
done

for i in /sys/block/sd*/queue/write_cache; do
	echo [Cache $i]
        cat $i
done

for i in /sys/block/dm*/queue/write_cache; do
        echo [Cache $i]
        cat $i
done

for i in /sys/block/sd*/queue/nomerges; do
	echo [Nomerges $i]
	cat $i
done

for i in /sys/block/dm*/queue/nomerges; do
	echo [nomerges $i]
	cat $i
done


