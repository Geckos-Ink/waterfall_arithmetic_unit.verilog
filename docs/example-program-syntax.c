// Example program (the c format is a reference for syntac highlight, the real format maybe .cw)


void main(){
    float[] input_data = system.receive_data(/*...*/)
    calculate calc = new calculate()
    calc.calculate(input_data);
}

space calculate {
    // [] behave like a "list" or anyway a dynamic size array
    float[] data; // substack, useful to determine best location where to save or cache (in circuit ram or on external DRAM)
    sub_space sub_spaces[8] ;
    DRAM float data_forced_on_ram[128]; // example of a variable forced on RAM

    void init(){ // if present, 

    }

    float calculate(float init_data[]){
        data = init_data; // or this.data = ...  

        for(int i=0; i<sub_spaces.count; i++){
            sub_spaces[i].calc_subdata(); // compile will include a concurrent checker and autotuning for automatically parallelize operations where possible
        }
    }

    space sub_space {
        float subdata[256];

        float[] calc_subdata(){
            // some calcs between this.data (accessible because in upper stack) and subdata
            // in the facts, they behave as pointers, that are compatible for make instant changes on external variables, but a classic "pointer" in a WAU has a different meaning, unless a variable explicitly on DRAM            
        }
    }
}